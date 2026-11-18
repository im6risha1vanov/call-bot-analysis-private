"""Draft, simulation and publication for the existing automations engine."""
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web

from . import admin_store as admin, automations as engine, company


def summarize(spec):
    spec = admin.decoded(spec)
    return {**spec, 'actions': [
        {k: v for k, v in action.items() if k != 'url'} | ({'url_configured': True} if 'url' in action else {})
        for action in spec['actions']
    ]}


def version_view(row):
    return {'version': row['id'], 'name': row['name'], 'spec': summarize(row['spec']),
            'simulated': row['simulated_at'] is not None,
            'published_at': row['published_at'].isoformat() if row['published_at'] else None}


async def get(pool, client_id, rule_id, *, lock=False):
    row = await pool.fetchrow(
        'SELECT * FROM automations WHERE id=$1 AND client_id=$2' + (' FOR UPDATE' if lock else ''),
        rule_id, client_id)
    if row is None:
        raise web.HTTPNotFound(reason='Правило не найдено.')
    return row


async def versions(pool, client_id, rule_id):
    row = await get(pool, client_id, rule_id)
    stored = await pool.fetch(
        'SELECT * FROM crm_automation_versions WHERE automation_id=$1 ORDER BY id DESC', rule_id)
    return {'id': rule_id, 'enabled': row['enabled'], 'active_version': row['admin_version_id'],
            'paused_reason': row['paused_reason'], 'versions': [version_view(v) for v in stored]}


async def listing(pool, client_id):
    result = await engine.overview(pool, client_id, include_hooks=False)
    for item in result['automations']:
        item.pop('text')
        item['spec'] = summarize(item['spec'])
    for account in result['accounts']:
        account.pop('error', None)
    return result


async def draft(conn, who, client_id, rule_id, data):
    await admin.client(conn, client_id, lock=True)
    await company.require(conn, client_id, 'automations')
    if rule_id is not None:
        old = await get(conn, client_id, rule_id, lock=True)
        latest = await conn.fetchrow(
            'SELECT * FROM crm_automation_versions WHERE automation_id=$1 ORDER BY id DESC LIMIT 1', rule_id)
    else:
        old = latest = None
    admin.check_version(data, latest['id'] if latest else 0)
    if 'restore_version' in data:
        source = await conn.fetchrow(
            '''SELECT * FROM crm_automation_versions WHERE automation_id=$1 AND id=$2
               AND published_at IS NOT NULL''', rule_id, data['restore_version'])
        if source is None:
            raise web.HTTPNotFound(reason='Версия правила не найдена.')
        name, spec = source['name'], admin.decoded(source['spec'])
    else:
        name, spec = data.get('name'), data.get('spec')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise ValueError('Укажите название правила от 1 до 120 символов.')
    clean = engine.validate(spec, await engine.accounts(conn, client_id))
    # JSONB replaces the previous draft in this transaction. The published version stays active.
    if old is None:
        rule_id = await conn.fetchval(
            '''INSERT INTO automations(client_id,name,spec,admin_managed,created_by)
               VALUES($1,$2,$3,true,$4) RETURNING id''', client_id, name.strip(), admin.as_json(clean), str(who.invite_id))
    elif not old['admin_managed']:
        archived = await conn.fetchval(
            '''INSERT INTO crm_automation_versions(automation_id,name,spec,published_at)
               VALUES($1,$2,$3,$4) RETURNING id''', rule_id, old['name'], old['spec'], old['updated_at'])
        await conn.execute('UPDATE automations SET admin_managed=true,admin_version_id=$2 WHERE id=$1',
                           rule_id, archived)
    await conn.execute(
        'DELETE FROM crm_automation_versions WHERE automation_id=$1 AND published_at IS NULL', rule_id)
    row = await conn.fetchrow(
        '''INSERT INTO crm_automation_versions(automation_id,name,spec) VALUES($1,$2,$3)
           RETURNING *''', rule_id, name.strip(), admin.as_json(clean))
    after = version_view(row)
    await admin.audit(conn, who, client_id, 'automations', rule_id, 'draft',
                      version_view(latest) if latest else None, after)
    return {'id': rule_id, **after}


async def current_draft(conn, client_id, rule_id, data):
    rule = await get(conn, client_id, rule_id, lock=True)
    row = await conn.fetchrow(
        'SELECT * FROM crm_automation_versions WHERE automation_id=$1 AND published_at IS NULL', rule_id)
    if row is None:
        raise web.HTTPConflict(reason='Создайте черновик правила.')
    admin.check_version(data, row['id'])
    return rule, row


async def simulate(conn, who, client_id, rule_id, data):
    _, row = await current_draft(conn, client_id, rule_id, data)
    spec = engine.validate(admin.decoded(row['spec']), await engine.accounts(conn, client_id))
    kind = spec['trigger']['type']
    payload = data.get('payload', engine.TRIGGERS[kind]['sample'])
    if not isinstance(payload, dict):
        raise ValueError('Данные примера должны быть объектом.')
    steps = []
    for step in spec['actions']:
        rendered = {k: engine.render(step[k], payload) for k in ('subject', 'text') if k in step}
        if 'row' in step:
            rendered['row'] = [engine.render(v, payload) for v in step['row']]
        steps.append({'type': step['type'], 'account': step.get('account'), **rendered})
    result = {'simulation': True, 'version': row['id'],
              'conditions': [engine.matches([c], payload) for c in spec['if']],
              'conditions_pass': engine.matches(spec['if'], payload), 'steps': steps}
    await conn.execute('UPDATE crm_automation_versions SET simulated_at=now() WHERE id=$1', row['id'])
    await admin.audit(conn, who, client_id, 'automations', rule_id, 'simulate',
                      after={'version': row['id'], 'conditions_pass': result['conditions_pass']})
    return result


async def publish(conn, who, client_id, rule_id, data):
    client = await admin.client(conn, client_id, lock=True)
    await company.require(conn, client_id, 'automations')
    rule, row = await current_draft(conn, client_id, rule_id, data)
    if row['simulated_at'] is None:
        raise web.HTTPConflict(reason='Проверьте эту версию правила в симуляции.')
    limit = await conn.fetchval('SELECT max_active FROM automation_limits WHERE client_id=$1', client_id)
    active = await conn.fetchval(
        'SELECT count(*) FROM automations WHERE client_id=$1 AND enabled AND id<>$2', client_id, rule_id)
    if limit is not None and active >= limit:
        raise ValueError(f'По тарифу можно до {limit} включённых сценариев.')
    spec = engine.validate(admin.decoded(row['spec']), await engine.accounts(conn, client_id))
    trigger = spec['trigger']
    local = datetime.now(ZoneInfo(client['timezone'] or 'Europe/Moscow'))
    last = local.date().isoformat() if trigger['type'] == 'schedule.daily' and local.strftime('%H:%M') >= trigger['time'] else None
    hook = (rule['hook_token'] or secrets.token_urlsafe(24)) if trigger['type'] == 'webhook.in' else None
    await conn.execute(
        '''UPDATE automations SET name=$2,spec=$3,enabled=true,paused_reason=NULL,last_fired=$4,
               hook_token=$5,admin_version_id=$6,updated_at=now() WHERE id=$1''',
        rule_id, row['name'], admin.as_json(spec), last, hook, row['id'])
    published = await conn.fetchrow(
        'UPDATE crm_automation_versions SET published_at=clock_timestamp() WHERE id=$1 RETURNING *', row['id'])
    after = version_view(published)
    await admin.audit(conn, who, client_id, 'automations', rule_id, 'publish',
                      {'version': rule['admin_version_id'], 'enabled': rule['enabled']}, after)
    return {'id': rule_id, 'enabled': True, **after}


async def pause(conn, who, client_id, rule_id, data):
    rule = await get(conn, client_id, rule_id, lock=True)
    admin.check_version(data, rule['admin_version_id'] or 0)
    await conn.execute('UPDATE automations SET enabled=false,updated_at=now() WHERE id=$1', rule_id)
    after = {'id': rule_id, 'enabled': False, 'version': rule['admin_version_id'] or 0}
    await admin.audit(conn, who, client_id, 'automations', rule_id, 'pause', {'enabled': rule['enabled']}, after)
    return after


async def runs(pool, client_id, rule_id):
    await get(pool, client_id, rule_id)
    rows = await pool.fetch(
        '''SELECT id,test,created_at,admin_version_id,steps,
                  CASE WHEN status='running' AND created_at < now() - interval '10 minutes'
                       THEN 'delivery_unknown' ELSE status END AS status
           FROM automation_runs WHERE client_id=$1 AND automation_id=$2 ORDER BY id DESC LIMIT 100''',
        client_id, rule_id)
    return [{'id': r['id'], 'status': r['status'], 'test': r['test'], 'at': r['created_at'].isoformat(),
             'version': r['admin_version_id'],
             'steps': [{k: v for k, v in step.items() if k in {'i', 'type', 'ok', 'status'}}
                       for step in admin.decoded(r['steps'])]} for r in rows]
