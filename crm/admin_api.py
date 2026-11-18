"""Authenticated HTTP routes for the platform owner's administration API."""
import hmac
import os
import re

from aiohttp import web

from . import admin_integrations as integrations, admin_permissions, admin_pipelines as pipelines
from . import admin_rules as rules, admin_store as admin, auth, company, platform


PREFIX = '/api/platform/admin'
COMPANY = r'/companies/{company_id:\d{1,9}}'
ACCOUNT = r'/accounts/{account_id:\d{1,18}}'
RULE = r'/automations/{rule_id:\d{1,18}}'
PIPELINE = '/pipelines/{pipeline}'
KEY = re.compile(r'[A-Za-z0-9._-]{12,96}\Z')


def number(value):
    value = str(value)
    if not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) < 1:
        raise ValueError('Некорректный идентификатор.')
    return int(value)


async def commit(pool, who, request, data, command):
    request_key = request.headers.get('Idempotency-Key')
    if request_key is None or not KEY.fullmatch(request_key):
        raise ValueError('Передайте Idempotency-Key из 12–96 символов.')
    secret = os.environ.get('ENCRYPTION_KEY')
    if not secret:
        raise web.HTTPServiceUnavailable(reason='Не настроено шифрование админки.')
    payload = admin.as_json([request.method, request.path, data]).encode()
    fingerprint = hmac.digest(secret.encode(), payload, 'sha256').hex()
    async with pool.acquire() as conn, conn.transaction():
        claimed = await conn.fetchval(
            '''INSERT INTO crm_admin_requests(actor_id,request_key,fingerprint) VALUES($1,$2,$3)
               ON CONFLICT DO NOTHING RETURNING true''', who.invite_id, request_key, fingerprint)
        if not claimed:
            saved = await conn.fetchrow(
                'SELECT fingerprint,response FROM crm_admin_requests WHERE actor_id=$1 AND request_key=$2',
                who.invite_id, request_key)
            if not hmac.compare_digest(saved['fingerprint'], fingerprint):
                raise web.HTTPConflict(reason='Этот ключ уже использован для другого запроса.')
            return admin.decoded(saved['response'])
        result = await command(conn)
        await conn.execute(
            '''UPDATE crm_admin_requests SET response=$3 WHERE actor_id=$1 AND request_key=$2''',
            who.invite_id, request_key, admin.as_json(result))
        return result


def routes(viewer, body, pool_key):
    registered = []

    def register(method, path):
        def decorate(handler):
            async def handle(request):
                pool = request.app[pool_key]
                who = await viewer(request)
                await admin.require(pool, who)
                match = request.match_info
                client_id = number(match['company_id']) if 'company_id' in match else None
                if method == 'GET':
                    if client_id is not None:
                        await admin.client(pool, client_id)
                    result = await handler(pool, who, client_id, match, request.query)
                else:
                    if os.getenv('CRM_PLATFORM_ADMIN_WRITES') != '1':
                        raise web.HTTPForbidden(reason='Админка открыта для просмотра.')
                    data = await body(request)

                    async def run(conn):
                        if client_id is not None:
                            await admin.client(conn, client_id)
                        return await handler(conn, who, client_id, match, data)

                    result = await commit(pool, who, request, data, run)
                code = 202 if path == COMPANY + '/integrations/{kind}/check' else 200
                return web.json_response(result, status=code)
            registered.append(web.route(method, PREFIX + path, handle))
            return handler
        return decorate

    @register('GET', '/catalog')
    async def catalog(pool, who, client_id, path, query):
        return {
            'modules': company.MODULES,
            'settings': {
                'callcenter_sheet': {'type': 'string', 'label': 'Таблица колл-центра'},
                'contract_email': {'type': 'string', 'label': 'Почта для договоров'},
                'ai_day_limit_rub': {'type': 'number', 'label': 'Дневной лимит ИИ', 'unit': 'RUB/day'},
            },
            'roles': admin.ROLES, 'sections': tuple(admin_permissions.SECTIONS),
            'actions': admin_permissions.ACTIONS, 'write_enabled': os.getenv('CRM_PLATFORM_ADMIN_WRITES') == '1',
        }

    @register('GET', '/companies')
    async def companies(pool, who, client_id, path, query):
        search = query.get('q', '').strip().casefold()
        after = number(query['after']) if 'after' in query else 0
        rows = await pool.fetch(
            '''SELECT c.id,coalesce(s.name,'') AS name FROM clients c
               LEFT JOIN company_settings s ON c.id=s.client_id WHERE c.id>$1 ORDER BY c.id''', after)
        home = await platform.home_client_id(pool)
        items = []
        for row in rows:
            cid = row['id']
            name = row['name'] or ('Averon' if cid == home else f'Компания {cid}')
            if search in name.casefold() or search in str(cid):
                items.append({'id': cid, 'name': name, 'home': cid == home})
            if len(items) == 101:
                break
        return {'items': items[:100], 'next_after': items[99]['id'] if len(items) > 100 else None}

    @register('POST', '/companies')
    async def create(pool, who, client_id, path, data):
        return await admin.create_company(pool, who, data)

    @register('GET', COMPANY)
    async def detail(pool, who, client_id, path, query):
        return await admin.detail(pool, client_id)

    @register('PATCH', COMPANY + '/settings')
    async def settings(pool, who, client_id, path, data):
        return await admin.update_company(pool, who, client_id, data)

    @register('POST', COMPANY + r'/settings/restore/{entry_id:\d{1,18}}')
    async def restore_settings(pool, who, client_id, path, data):
        return await admin.restore_settings(pool, who, client_id, number(path['entry_id']), data)

    @register('GET', COMPANY + '/accounts')
    async def accounts(pool, who, client_id, path, query):
        rows = await pool.fetch(
            '''SELECT id,role,login,extension,client_key,name,platform,revoked_at IS NOT NULL AS disabled
               FROM crm_invites WHERE client_id=$1 AND id>$2 ORDER BY id LIMIT 101''',
            client_id, number(query['after']) if 'after' in query else 0)
        return {'items': [dict(r) for r in rows[:100]], 'next_after': rows[99]['id'] if len(rows) > 100 else None}

    @register('POST', COMPANY + '/accounts')
    async def create_account(pool, who, client_id, path, data):
        return await admin.create_account(pool, who, client_id, data)

    @register('GET', COMPANY + ACCOUNT + '/permissions')
    async def account_permissions(pool, who, client_id, path, query):
        item = await admin.account(pool, client_id, number(path['account_id']))
        role = await admin.permissions(pool, client_id, item['role'])
        return {**role, 'account': item, 'scope': 'assigned_records' if item['role'] == 'manager' else 'native_crm_role',
                'modules': (await company.info(pool, client_id))['modules']}

    @register('POST', COMPANY + ACCOUNT + '/{action:disable|role|password|sessions}')
    async def change_account(pool, who, client_id, path, data):
        return await admin.change_account(pool, who, client_id, number(path['account_id']), path['action'], data)

    @register('GET', COMPANY + '/permissions/{role}')
    async def permissions(pool, who, client_id, path, query):
        return await admin.permissions(pool, client_id, path['role'])

    @register('PATCH', COMPANY + '/permissions/{role}')
    async def change_permissions(pool, who, client_id, path, data):
        return await admin.update_permissions(pool, who, client_id, path['role'], data)

    @register('GET', COMPANY + '/pipelines')
    async def pipeline_list(pool, who, client_id, path, query):
        return {'items': await pipelines.listing(pool, client_id)}

    @register('GET', COMPANY + PIPELINE + '/versions')
    async def pipeline_versions(pool, who, client_id, path, query):
        return {'items': await pipelines.versions(pool, client_id, path['pipeline'])}

    @register('PUT', COMPANY + PIPELINE + '/draft')
    async def pipeline_save(pool, who, client_id, path, data):
        return await pipelines.save(pool, who, client_id, path['pipeline'], data)

    @register('GET', COMPANY + PIPELINE + '/preview')
    async def pipeline_preview(pool, who, client_id, path, query):
        return await pipelines.preview(pool, client_id, path['pipeline'], number(query.get('version', '')))

    @register('POST', COMPANY + PIPELINE + '/publish')
    async def pipeline_publish(pool, who, client_id, path, data):
        return await pipelines.publish(pool, who, client_id, path['pipeline'], data)

    @register('GET', COMPANY + PIPELINE + '/report')
    async def pipeline_report(pool, who, client_id, path, query):
        return await pipelines.report(pool, client_id, path['pipeline'], query.get('as_of'))

    @register('GET', COMPANY + '/automations')
    async def automation_list(pool, who, client_id, path, query):
        return await rules.listing(pool, client_id)

    @register('GET', COMPANY + RULE + '/versions')
    async def rule_versions(pool, who, client_id, path, query):
        return await rules.versions(pool, client_id, number(path['rule_id']))

    @register('POST', COMPANY + '/automations')
    @register('PUT', COMPANY + RULE + '/draft')
    async def rule_save(pool, who, client_id, path, data):
        return await rules.draft(pool, who, client_id, number(path['rule_id']) if 'rule_id' in path else None, data)

    @register('POST', COMPANY + RULE + '/{action:simulate|publish|pause}')
    async def rule_action(pool, who, client_id, path, data):
        command = {'simulate': rules.simulate, 'publish': rules.publish, 'pause': rules.pause}[path['action']]
        return await command(pool, who, client_id, number(path['rule_id']), data)

    @register('GET', COMPANY + RULE + '/runs')
    async def rule_runs(pool, who, client_id, path, query):
        return {'items': await rules.runs(pool, client_id, number(path['rule_id']))}

    @register('GET', COMPANY + '/integrations')
    async def integration_list(pool, who, client_id, path, query):
        return await integrations.listing(pool, client_id)

    @register('POST', COMPANY + '/integrations/{kind}/check')
    async def integration_check(pool, who, client_id, path, data):
        return await integrations.queue(pool, who, client_id, path['kind'], data)

    @register('POST', COMPANY + '/integrations/telegram')
    async def telegram(pool, who, client_id, path, data):
        return await integrations.telegram(pool, who, client_id, data)

    @register('POST', COMPANY + r'/integrations/accounts/{account_id:\d{1,18}}/disconnect')
    async def disconnect(pool, who, client_id, path, data):
        return await integrations.disconnect(pool, who, client_id, number(path['account_id']))

    @register('POST', COMPANY + RULE + '/webhook')
    async def rotate_hook(pool, who, client_id, path, data):
        return await integrations.rotate_hook(pool, who, client_id, number(path['rule_id']), data)

    @register('GET', COMPANY + r'/operations/{operation_id:\d{1,18}}')
    async def operation(pool, who, client_id, path, query):
        return await integrations.operation(pool, client_id, number(path['operation_id']))

    @register('GET', COMPANY + '/audit')
    async def audit(pool, who, client_id, path, query):
        before = number(query['before']) if 'before' in query else None
        return await admin.history(pool, client_id, before, query.get('resource'))

    return registered
