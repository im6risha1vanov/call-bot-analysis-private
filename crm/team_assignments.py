"""Company-scoped assignments, separate from private personal tasks."""
import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit
from aiohttp import web
from tools import Forbidden, Actor
from . import personal, deals


async def identity(db, who):
    tg = await personal.identity(db, who)
    if not tg or who.role not in ('director', 'manager') or not await db.fetchval(
        'SELECT 1 FROM employees WHERE client_id=$1 AND telegram_user_id=$2 AND archived_at IS NULL', who.company, tg):
        raise Forbidden('Командный раздел недоступен.')
    return (who.company, tg)


async def telegram_identity(db, actor):
    """Revalidate a Telegram actor and an active direct CRM invitation."""
    if not actor or actor.telegram_user_id not in personal.pilot_ids() or actor.role not in ('owner', 'head', 'manager'):
        raise Forbidden('Командный раздел недоступен.')
    role = 'director' if actor.role in ('owner', 'head') else 'manager'
    valid = await db.fetchval('''SELECT 1 FROM employees e JOIN crm_invites i ON i.client_id=e.client_id
        AND i.telegram_user_id=e.telegram_user_id AND i.revoked_at IS NULL AND i.role=$4
        WHERE e.client_id=$1 AND e.telegram_user_id=$2 AND e.role=$3 AND e.archived_at IS NULL''',
        actor.client_id, actor.telegram_user_id, actor.role, role)
    if not valid: raise Forbidden('Командный раздел недоступен.')
    return actor.client_id, actor.telegram_user_id


def parse(data, partial=False):
    fields = {'title', 'description', 'assignee_id', 'due_at', 'deal_id'}
    if not isinstance(data, dict) or set(data) - fields:
        raise ValueError('Неизвестные поля поручения.')
    out = {}
    for key, val in data.items():
        if key in ('title', 'description'):
            out[key] = personal.clean_text(val, key, key == 'title', 300 if key == 'title' else 4000)
        elif key == 'deal_id':
            if val is not None and (not isinstance(val, str) or len(val) != 10 or not val.isascii() or not val.isdigit()): raise ValueError('Неверная сделка.')
            out[key] = val
        elif key == 'assignee_id':
            if type(val) is not int or val <= 0: raise ValueError('Укажите исполнителя.')
            out[key] = val
        else:
            if val:
                try:
                    val = datetime.fromisoformat(val.replace('Z', '+00:00'))
                    if val.utcoffset() is None: raise ValueError()
                except (ValueError, TypeError, AttributeError): raise ValueError('Укажите дату с часовым поясом.')
            else: val = None
            out[key] = val
    if not partial and not {'title', 'assignee_id'} <= out.keys(): raise ValueError('Укажите название и исполнителя.')
    return out


def links(value):
    if not isinstance(value, list) or len(value) > 10: raise ValueError('Неверные ссылки.')
    for url in value:
        if not isinstance(url, str) or len(url) > 2048: raise ValueError('Неверная ссылка.')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in url):
            raise ValueError('Неверная ссылка.')
    return value


def serial(row):
    result = personal.serial(row)
    if isinstance(result.get('result_links'), str): result['result_links'] = json.loads(result['result_links'])
    return result


async def employee(conn, company, tg):
    if tg not in personal.pilot_ids() or not await conn.fetchval('''SELECT 1 FROM employees e JOIN crm_invites i
        ON i.client_id=e.client_id AND i.telegram_user_id=e.telegram_user_id AND i.revoked_at IS NULL
        WHERE e.client_id=$1 AND e.telegram_user_id=$2 AND e.archived_at IS NULL''', company, tg):
        raise web.HTTPNotFound(reason='Исполнитель недоступен.')


async def verify_deal(conn, company, people, deal_id):
    if deal_id is None: return
    for tg in set(people):
        row = await conn.fetchrow('SELECT role,extension FROM employees WHERE client_id=$1 AND telegram_user_id=$2 AND archived_at IS NULL', company, tg)
        if not row or row['role'] not in ('owner','head','manager'): raise Forbidden('Сделка недоступна.')
        await deals.one(conn, Actor(tg, company, row['role'], row['extension']), deal_id)

async def visible(conn, ident, item):
    """Keep task access while suppressing deals the caller cannot currently view."""
    item = dict(item)
    if item.get('deal_id'):
        try:
            await verify_deal(conn, ident[0], (ident[1],), item['deal_id'])
        except (Forbidden, ValueError, web.HTTPNotFound):
            item['deal_id'] = None
    return item


async def current_participant(conn, ident, task, *, lock=False):
    suffix = ' FOR UPDATE' if lock else ''
    row = await conn.fetchrow('SELECT * FROM crm_team_assignments WHERE id=$1 AND company_id=$2 AND (creator_id=$3 OR assignee_id=$3)' + suffix, task, *ident)
    if not row: raise web.HTTPNotFound(reason='Поручение не найдено.')
    return row


def key_value(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or not key.strip(): raise ValueError('Укажите ключ повторного запроса.')
    return key


async def retry(conn, ident, key, payload):
    key_value(key)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1, 0))', f'{ident[0]}:{ident[1]}:{key}')
    prior = await conn.fetchrow('SELECT fingerprint,response FROM crm_team_assignment_requests WHERE company_id=$1 AND actor_id=$2 AND request_key=$3', *ident, key)
    if prior:
        if prior['fingerprint'] != fingerprint: raise web.HTTPConflict(reason='Ключ уже использован для другого запроса.')
        return fingerprint, json.loads(prior['response']) if isinstance(prior['response'], str) else prior['response']
    return fingerprint, None


async def record(conn, ident, key, fingerprint, row, action):
    item = serial(row)
    await conn.execute('INSERT INTO crm_team_assignment_history(assignment_id,company_id,version,actor_id,action,snapshot) VALUES($1,$2,$3,$4,$5,$6::jsonb)', row['id'], ident[0], row['version'], ident[1], action, json.dumps(item, ensure_ascii=False))
    await conn.execute('INSERT INTO crm_team_assignment_requests(company_id,actor_id,request_key,fingerprint,assignment_id,response) VALUES($1,$2,$3,$4,$5,$6::jsonb)', *ident, key, fingerprint, row['id'], json.dumps(item, ensure_ascii=False))
    return item


async def create(conn, ident, normalized_data, source_meeting_id=None, source_item_id=None, idempotency_key=None):
    values = parse(normalized_data)
    fp, prior = await retry(conn, ident, idempotency_key, {'create': values, 'meeting': source_meeting_id, 'item': source_item_id})
    if prior is not None:
        await employee(conn, *ident)
        await current_participant(conn, ident, prior['id'], lock=True)
        return await visible(conn, ident, prior)
    if values['assignee_id'] == ident[1]: raise ValueError('Исполнитель должен отличаться от постановщика.')
    await employee(conn, ident[0], ident[1]); await employee(conn, ident[0], values['assignee_id'])
    await verify_deal(conn, ident[0], (ident[1], values['assignee_id']), values.get('deal_id'))
    row = await conn.fetchrow('''INSERT INTO crm_team_assignments(company_id,creator_id,assignee_id,title,description,due_at,source_meeting_id,source_item_id,deal_id)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *''', *ident, values['assignee_id'], values['title'], values.get('description',''), values.get('due_at'), source_meeting_id, source_item_id, values.get('deal_id'))
    return await record(conn, ident, idempotency_key, fp, row, 'create')


def transition(row, actor, action, data):
    creator, assignee, status = row['creator_id'] == actor, row['assignee_id'] == actor, row['status']
    rules = {'start': (assignee, {'assigned'}, 'in_progress'), 'block': (assignee, {'assigned','in_progress'}, status),
             'clear_blocker': (assignee, {'assigned','in_progress'}, status), 'submit': (assignee, {'assigned','in_progress'}, 'review'),
             'return': (creator, {'review'}, 'in_progress'), 'accept': (creator, {'review'}, 'accepted'),
             'cancel': (creator, {'assigned','in_progress','review'}, 'cancelled')}
    if action not in rules: raise web.HTTPNotFound()
    allowed, states, new = rules[action]
    if not allowed: raise Forbidden('Действие недоступно.')
    if status not in states: raise web.HTTPConflict(reason='Действие недоступно в текущем состоянии.')
    allowed_fields = {'block': {'blocker'}, 'submit': {'result_text','result_links'}, 'return': {'comment'}}.get(action, set())
    if set(data) - allowed_fields: raise ValueError('Неизвестные поля действия.')
    changes = {'status': new}
    if action == 'block': changes['blocker'] = personal.clean_text(data.get('blocker'), 'препятствие', True)
    if action == 'clear_blocker': changes['blocker'] = ''
    if action == 'submit':
        changes['blocker'] = ''
        changes['result_text'] = personal.clean_text(data.get('result_text'), 'результат', True)
        changes['result_links'] = links(data.get('result_links', []))
    if action == 'return': changes['review_note'] = personal.clean_text(data.get('comment'), 'комментарий', True)
    return changes


async def people(db, ident):
    rows = await db.fetch('''SELECT e.telegram_user_id AS id, COALESCE(NULLIF(MAX(e.full_name), ''), MAX(i.name), '') AS display_name
        FROM employees e JOIN crm_invites i ON i.client_id=e.client_id
        AND i.telegram_user_id=e.telegram_user_id AND i.revoked_at IS NULL
        WHERE e.client_id=$1 AND e.archived_at IS NULL AND e.telegram_user_id <> $2
        AND e.telegram_user_id=ANY($3::bigint[])
        AND ((e.role='manager' AND i.role='manager') OR (e.role IN ('owner','head') AND i.role='director')) GROUP BY e.telegram_user_id ORDER BY display_name, id''', *ident, list(personal.pilot_ids()))
    return [dict(r) for r in rows]


async def list_items(db, ident, mode='assigned'):
    if mode not in ('assigned','to_review','created'): raise ValueError('Неизвестный фильтр.')
    condition = {'assigned': 'assignee_id=$2', 'to_review': "creator_id=$2 AND status='review'", 'created': 'creator_id=$2'}[mode]
    rows = await db.fetch(f'SELECT * FROM crm_team_assignments WHERE company_id=$1 AND {condition} ORDER BY id DESC LIMIT 500', *ident)
    return [await visible(db, ident, serial(r)) for r in rows]


async def get_item(db, ident, task):
    return await visible(db, ident, serial(await current_participant(db, ident, task)))


async def change(conn, ident, task, version, action, data, key):
    if not isinstance(data, dict): raise ValueError('Неверный запрос.')
    if type(version) is not int or version < 1: raise ValueError('Укажите версию.')
    fp, prior = await retry(conn, ident, key, {'id': task, 'version': version, 'action': action, 'data': data})
    await employee(conn, *ident)
    row = await current_participant(conn, ident, task, lock=True)
    if prior is not None: return await visible(conn, ident, prior)
    if row['version'] != version: raise web.HTTPConflict(reason='Поручение уже изменено.')
    if action == 'edit':
        if row['creator_id'] != ident[1]: raise Forbidden('Действие недоступно.')
        if row['status'] not in ('assigned','in_progress'): raise web.HTTPConflict(reason='Редактирование недоступно.')
        values = parse(data, partial=True)
        if not values: raise ValueError('Укажите изменения.')
        if 'assignee_id' in values:
            if values['assignee_id'] == row['creator_id']: raise ValueError('Исполнитель должен отличаться от постановщика.')
            await employee(conn, ident[0], values['assignee_id'])
        if 'deal_id' in values or 'assignee_id' in values:
            await verify_deal(conn, ident[0], (ident[1], values.get('assignee_id', row['assignee_id'])), values.get('deal_id', row['deal_id']))
    else: values = transition(row, ident[1], action, data)
    if 'result_links' in values: values['result_links'] = json.dumps(values['result_links'])
    sets = ','.join(f'{k}=${i+3}' + ('::jsonb' if k == 'result_links' else '') for i,k in enumerate(values))
    row = await conn.fetchrow(f'UPDATE crm_team_assignments SET {sets},version=version+1,updated_at=now() WHERE id=$1 AND company_id=$2 RETURNING *', task, ident[0], *values.values())
    return await visible(conn, ident, await record(conn, ident, key, fp, row, action))


def routes(viewer, body, pool_key):
    async def context(request):
        pool = request.app[pool_key]
        return pool, await identity(pool, await viewer(request))
    def number(request):
        try:
            value = int(request.match_info['id'])
            if value <= 0: raise ValueError()
            return value
        except ValueError: raise web.HTTPNotFound()
    async def listing(request):
        pool, ident = await context(request)
        return web.json_response({'assignments': await list_items(pool, ident, request.query.get('filter', 'assigned'))})
    async def people_route(request):
        pool, ident = await context(request)
        return web.json_response({'people': await people(pool, ident)})
    async def mine(request):
        pool, ident = await context(request)
        rows = await pool.fetch('SELECT * FROM crm_team_assignments WHERE company_id=$1 AND assignee_id=$2 ORDER BY id DESC', *ident)
        private = await pool.fetch('SELECT t.* FROM crm_personal_tasks t JOIN crm_personal_profiles p ON p.id=t.owner_id WHERE p.telegram_user_id=$1 ORDER BY t.id DESC', ident[1])
        return web.json_response({'items': [{'kind':'team', **await visible(pool, ident, serial(r))} for r in rows] + [{'kind':'personal', **personal.serial(r)} for r in private]})
    async def detail(request):
        pool, ident = await context(request); task = number(request)
        row = await current_participant(pool, ident, task)
        if request.path.endswith('/history'):
            hist = await pool.fetch('SELECT version,actor_id,action,snapshot,created_at FROM crm_team_assignment_history WHERE assignment_id=$1 AND company_id=$2 ORDER BY version', task, ident[0])
            return web.json_response({'history': [{**personal.serial(h), 'snapshot': await visible(pool, ident, json.loads(h['snapshot']) if isinstance(h['snapshot'], str) else h['snapshot'])} for h in hist]})
        return web.json_response({'assignment': await visible(pool, ident, serial(row))})
    async def post(request):
        pool, ident = await context(request); data = await body(request)
        async with pool.acquire() as conn, conn.transaction():
            item = await create(conn, ident, data, idempotency_key=request.headers.get('Idempotency-Key'))
        return web.json_response({'assignment': await visible(pool, ident, item)}, status=201)
    async def change_route(request):
        pool, ident = await context(request); task = number(request); data = await body(request)
        if not isinstance(data, dict): raise ValueError('Неверный запрос.')
        version = data.pop('version', None)
        if type(version) is not int or version < 1: raise ValueError('Укажите версию.')
        action = request.match_info.get('action', 'edit'); key = request.headers.get('Idempotency-Key')
        async with pool.acquire() as conn, conn.transaction():
            item = await change(conn, ident, task, version, action, data, key)
        return web.json_response({'assignment': item})
    return [web.get('/api/team/assignments', listing), web.post('/api/team/assignments', post),
            web.get('/api/team/mine', mine), web.get('/api/team/assignments/people', people_route), web.get('/api/team/assignments/{id}', detail),
            web.get('/api/team/assignments/{id}/history', detail), web.patch('/api/team/assignments/{id}', change_route),
            web.post('/api/team/assignments/{id}/{action}', change_route)]
