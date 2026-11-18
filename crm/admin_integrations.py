"""Integration checks use the application worker and store sanitized results."""
import asyncio
import json
import logging
import os
import re
from types import SimpleNamespace

from aiohttp import web
from asyncpg import UniqueViolationError

import mango_client
from crypto_util import decrypt, encrypt
from . import admin_store as admin, automation_blocks as blocks, automations, company


LOG = logging.getLogger(__name__)


async def listing(pool, client_id):
    client = await admin.client(pool, client_id)
    found = await pool.fetch('SELECT * FROM crm_admin_integrations WHERE client_id=$1 ORDER BY kind', client_id)
    status = {r['kind']: {'status': r['status'], 'error': r['error'],
                         'checked_at': r['checked_at'].isoformat(),
                         'last_success_at': r['last_success_at'].isoformat() if r['last_success_at'] else None}
              for r in found}
    accounts = [{k: a[k] for k in ('id', 'kind', 'title', 'status')}
                for a in (await automations.accounts(pool, client_id)).values()]
    hooks = await pool.fetch(
        '''SELECT id,name,enabled,hook_token IS NOT NULL AS configured FROM automations
           WHERE client_id=$1 AND spec->'trigger'->>'type'='webhook.in' ORDER BY id''', client_id)
    return {'mango': {'connected': client['mango'], 'processing': client['processing_enabled']},
            'telegram': bool(os.getenv('BOT_TOKEN')), 'accounts': accounts,
            'webhooks': [dict(r) for r in hooks], 'checks': status}


async def queue(conn, who, client_id, kind, data):
    if kind == 'mango':
        inputs = {k: data.get(k) for k in ('key', 'salt')}
        if not any(v is not None for v in inputs.values()):
            inputs = {}
        elif any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 200 for v in inputs.values()):
            raise ValueError('Нужны уникальный код ВАТС и ключ подписи.')
        else:
            inputs = {k: v.strip() for k, v in inputs.items()}
    elif kind == 'sheets':
        await company.require(conn, client_id, 'automations')
        if not isinstance(data.get('url'), str) or not blocks.sheet_id(data['url']):
            raise ValueError('Укажите ссылку на Google Таблицу.')
        sheet = data.get('sheet', '')
        if not isinstance(sheet, str) or len(sheet) > 120:
            raise ValueError('Недопустимое имя листа.')
        inputs = {'url': data['url'], 'sheet': sheet}
    else:
        raise web.HTTPNotFound(reason='Интеграция не найдена.')
    try:
        job_id = await conn.fetchval(
            '''INSERT INTO crm_admin_operations(client_id,actor_id,kind,input_enc)
               VALUES($1,$2,$3,$4) RETURNING id''', client_id, who.invite_id, kind, encrypt(admin.as_json(inputs)))
    except UniqueViolationError:
        raise web.HTTPConflict(reason='Проверка этой интеграции уже выполняется.')
    result = {'id': job_id, 'status': 'queued'}
    await admin.audit(conn, who, client_id, 'integrations', kind, 'queue', after=result)
    return result


async def telegram(conn, who, client_id, data):
    await company.require(conn, client_id, 'automations')
    code = data.get('code')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9]{24,40}', code):
        raise ValueError('Укажите код подключения из 24–40 латинских букв и цифр.')
    try:
        account_id = await conn.fetchval(
            '''INSERT INTO automation_accounts(client_id,kind,title,connect_code,created_by)
               VALUES($1,'telegram','Telegram: ждём подключения',$2,$3) RETURNING id''',
            client_id, code, str(who.invite_id))
    except UniqueViolationError:
        raise web.HTTPConflict(reason='Этот временный код уже используется.')
    result = {'id': account_id, 'status': 'pending'}
    await admin.audit(conn, who, client_id, 'integrations', 'telegram', 'connect', after=result)
    return result


async def rotate_hook(conn, who, client_id, rule_id, data):
    token = data.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,80}', token):
        raise ValueError('Укажите новый токен вебхука из 32–80 символов.')
    try:
        changed = await conn.fetchval(
            '''UPDATE automations SET hook_token=$3 WHERE id=$1 AND client_id=$2
               AND spec->'trigger'->>'type'='webhook.in' RETURNING id''',
            rule_id, client_id, token)
    except UniqueViolationError:
        raise web.HTTPConflict(reason='Токен уже используется.')
    if changed is None:
        raise web.HTTPNotFound(reason='Вебхук не найден.')
    result = {'id': rule_id, 'configured': True}
    await admin.audit(conn, who, client_id, 'integrations', rule_id, 'rotate', after=result)
    return result


async def disconnect(conn, who, client_id, account_id):
    await admin.client(conn, client_id, lock=True)
    if not await conn.fetchval('SELECT id FROM automation_accounts WHERE id=$1 AND client_id=$2 FOR UPDATE',
                               account_id, client_id):
        raise web.HTTPNotFound(reason='Подключение не найдено.')
    if await conn.fetchval(
        '''SELECT 1 FROM crm_automation_versions v JOIN automations a ON a.id=v.automation_id
           WHERE a.client_id=$1 AND v.published_at IS NULL AND EXISTS (
               SELECT 1 FROM jsonb_array_elements(v.spec->'actions') x WHERE (x->>'account')::bigint=$2
           ) LIMIT 1''', client_id, account_id):
        raise web.HTTPConflict(reason='Подключение используется в черновике правила.')
    await automations.delete_account(conn, client_id, account_id)
    await admin.audit(conn, who, client_id, 'integrations', account_id, 'disconnect')
    return {'id': account_id, 'deleted': True}


async def operation(pool, client_id, operation_id):
    row = await pool.fetchrow(
        '''SELECT id,kind,status,result,created_at,started_at,finished_at
           FROM crm_admin_operations WHERE id=$1 AND client_id=$2''', operation_id, client_id)
    if row is None:
        raise web.HTTPNotFound(reason='Операция не найдена.')
    return {**dict(row), 'result': admin.decoded(row['result']),
            **{field: row[field].isoformat() if row[field] else None
               for field in ('created_at', 'started_at', 'finished_at')}}


async def prepare(pool, job, data):
    client_id = job['client_id']
    if job['kind'] == 'mango':
        if data:
            key, salt = data['key'], data['salt']
        else:
            row = await pool.fetchrow('SELECT vpbx_api_key_enc,vpbx_api_salt_enc FROM clients WHERE id=$1', client_id)
            key, salt = (decrypt(row[name]) for name in ('vpbx_api_key_enc', 'vpbx_api_salt_enc'))
        await mango_client.call('account/balance', key, salt, {}, timeout=12)
        return {'credentials': (encrypt(key), encrypt(salt)) if data else None}
    spreadsheet = blocks.sheet_id(data['url'])
    info = await blocks.sheets_probe(spreadsheet)
    sheet = data['sheet'].strip() or info['sheets'][0]
    if sheet not in info['sheets']:
        raise ValueError('Лист не найден.')
    return {'title': f'{info["title"]} / {sheet}'[:120],
            'credentials': encrypt(json.dumps({'spreadsheet_id': spreadsheet, 'sheet': sheet}))}


async def process(pool):
    await pool.execute(
        '''UPDATE crm_admin_operations SET status='delivery_unknown',input_enc=NULL,finished_at=now()
           WHERE status='running' AND started_at < now() - interval '5 minutes' ''')
    job = await pool.fetchrow(
        '''UPDATE crm_admin_operations SET status='running',started_at=now() WHERE id=(
               SELECT id FROM crm_admin_operations WHERE status='queued'
               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
           ) RETURNING *''')
    if job is None:
        return
    kind = job['kind']
    client_id = job['client_id']
    who = SimpleNamespace(invite_id=job['actor_id'])
    try:
        await asyncio.wait_for(admin.require(pool, await operation_viewer(pool, who.invite_id)), timeout=5)
        result = await asyncio.wait_for(prepare(pool, job, json.loads(decrypt(job['input_enc']))), timeout=60)
        error = None
    except Exception as exc:
        LOG.warning('Integration %s check %s failed (%s)', kind, job['id'], type(exc).__name__)
        result = None
        error = 'Проверка не прошла. Проверьте подключение и доступ к сервису.'
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval(
            "SELECT id FROM crm_admin_operations WHERE id=$1 AND status='running' FOR UPDATE", job['id']):
            return
        status = 'failed' if error else 'ok'
        public = {'error': error} if error else {'connected': True}
        if result and result['credentials']:
            if kind == 'mango':
                await conn.execute(
                    'UPDATE clients SET vpbx_api_key_enc=$2,vpbx_api_salt_enc=$3,processing_enabled=true WHERE id=$1',
                    client_id, *result['credentials'])
            else:
                public['account_id'] = await conn.fetchval(
                    '''INSERT INTO automation_accounts(client_id,kind,title,data_enc,status,created_by)
                       VALUES($1,'sheets',$2,$3,'ready',$4) RETURNING id''',
                    client_id, result['title'], result['credentials'], str(who.invite_id))
        await conn.execute(
            '''INSERT INTO crm_admin_integrations(client_id,kind,status,error,last_success_at)
               VALUES($1,$2,$3,$4,CASE WHEN $3='ok' THEN now() END)
               ON CONFLICT (client_id,kind) DO UPDATE
               SET status=excluded.status,error=excluded.error,checked_at=now(),
                   last_success_at=coalesce(excluded.last_success_at,crm_admin_integrations.last_success_at)''',
            client_id, kind, status, error)
        await admin.audit(conn, who, client_id, 'integrations', kind, status, after=public)
        await conn.execute(
            '''UPDATE crm_admin_operations SET status=$2,input_enc=NULL,result=$3,finished_at=now()
               WHERE id=$1''', job['id'], status, admin.as_json(public))


async def operation_viewer(pool, invite_id):
    row = await pool.fetchrow(
        'SELECT role,client_id FROM crm_invites WHERE id=$1 AND revoked_at IS NULL', invite_id)
    if row is None:
        raise ValueError('Учётная запись отключена.')
    return SimpleNamespace(invite_id=invite_id, role=row['role'], company=row['client_id'], test=False,
                           support=False, support_by=None)


async def loop(pool):
    while True:
        try:
            await process(pool)
        except Exception as exc:
            LOG.error('Integration worker error (%s)', type(exc).__name__)
        await asyncio.sleep(2)
