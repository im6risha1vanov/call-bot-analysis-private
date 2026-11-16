"""Private personal workspace: all operations are scoped to the verified person."""
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web
from tools import Forbidden

STATUSES = {'planned', 'in_progress', 'done', 'cancelled'}
FIELDS = ('title', 'project_id', 'expected_result', 'result_note', 'blocker', 'status', 'due_at', 'planned_day', 'reminder_at')


def pilot_ids():
    value = os.getenv('CRM_PERSONAL_PILOT_TELEGRAM_IDS', '').strip()
    if not value:
        return set()
    parts = value.split(',')
    if any(not p.strip().isdigit() or int(p.strip()) <= 0 for p in parts):
        return set()
    return {int(p.strip()) for p in parts}


async def identity(pool, who):
    if not who or who.test or who.support or who.support_by is not None or not who.invite_id or not who.actor:
        return None
    tg = who.actor.telegram_user_id
    if not tg or tg not in pilot_ids():
        return None
    valid = await pool.fetchval('''SELECT 1 FROM crm_invites WHERE id=$1 AND revoked_at IS NULL
        AND telegram_user_id=$2 AND client_id=$3''', who.invite_id, tg, who.actor.client_id)
    return tg if valid else None


def clean_text(value, name, required=False, limit=4000):
    if not isinstance(value, str):
        raise ValueError(f'Неверное поле: {name}.')
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise ValueError(f'Неверное поле: {name}.')
    return value


def parse_task(data, partial=False):
    if not isinstance(data, dict) or set(data) - set(FIELDS) - {'version'}:
        raise ValueError('Неизвестные поля задачи.')
    out = {}
    for field in FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in ('title', 'expected_result', 'result_note', 'blocker'):
            value = clean_text(value, field, field == 'title', 300 if field == 'title' else 4000)
        elif field == 'status':
            if not isinstance(value, str) or value not in STATUSES:
                raise ValueError('Неверный статус.')
        elif field == 'project_id':
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError('Неверный проект.')
        elif value is not None and value != '':
            try:
                value = date.fromisoformat(value) if field == 'planned_day' else datetime.fromisoformat(value.replace('Z', '+00:00'))
                if field != 'planned_day' and value.utcoffset() is None:
                    raise ValueError()
            except (TypeError, ValueError, AttributeError):
                raise ValueError('Неверная дата или время; укажите часовой пояс.')
        else:
            value = None
        out[field] = value
    if not partial and 'title' not in out:
        raise ValueError('Укажите название задачи.')
    return out


def serial(row):
    return {k: v.isoformat() if isinstance(v, (date, datetime)) else v for k, v in dict(row).items()}


async def save_task(pool, owner_id, values, task_id=None, version=None, event_key=None, state_identity=None):
    creating = task_id is None
    async with pool.acquire() as conn, conn.transaction():
        if event_key is not None:
            claimed = await conn.fetchval('INSERT INTO crm_personal_telegram_events(event_key) VALUES($1) ON CONFLICT DO NOTHING RETURNING 1', event_key)
            if not claimed:
                return None
        if state_identity is not None:
            user_id, token = state_identity
            consumed = await conn.fetchval("DELETE FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND token=$2 AND action <> 'idle' AND updated_at > now() - interval '1 day' RETURNING 1", user_id, token)
            if not consumed:
                return None
        if 'project_id' in values and values['project_id'] is not None:
            if not await conn.fetchval('SELECT 1 FROM crm_personal_projects WHERE id=$1 AND owner_id=$2', values['project_id'], owner_id):
                raise web.HTTPNotFound(reason='Проект не найден.')
        if creating:
            keys = list(values)
            cols = ','.join(keys)
            slots = ','.join('$' + str(i + 2) for i in range(len(keys)))
            row = await conn.fetchrow(f'INSERT INTO crm_personal_tasks(owner_id,{cols}) VALUES($1,{slots}) RETURNING *', owner_id, *values.values())
        else:
            current = await conn.fetchrow('SELECT * FROM crm_personal_tasks WHERE id=$1 AND owner_id=$2 FOR UPDATE', task_id, owner_id)
            if not current:
                raise web.HTTPNotFound(reason='Задача не найдена.')
            if current['version'] != version:
                raise web.HTTPConflict(reason='Задача уже изменена. Обновите страницу.')
            assignments = ','.join(f'{key}=${i+3}' for i, key in enumerate(values))
            row = await conn.fetchrow(f'UPDATE crm_personal_tasks SET {assignments},version=version+1,updated_at=now() WHERE id=$1 AND owner_id=$2 RETURNING *', task_id, owner_id, *values.values())
        if state_identity is not None:
            await conn.execute("INSERT INTO crm_personal_telegram_state(telegram_user_id,action,token) VALUES($1,'idle',$2) ON CONFLICT(telegram_user_id) DO UPDATE SET action='idle', task_id=NULL, version=NULL, token=excluded.token, updated_at=now()", user_id, token)
        snapshot = json.dumps(serial(row), ensure_ascii=False)
        await conn.execute('INSERT INTO crm_personal_task_history(task_id,owner_id,version,snapshot) VALUES($1,$2,$3,$4::jsonb)', row['id'], owner_id, row['version'], snapshot)
    return row


def routes(viewer, body, pool_key):
    async def owner(request, required=True):
        pool = request.app[pool_key]
        tg = await identity(pool, await viewer(request))
        if tg is None:
            raise Forbidden('Личный раздел недоступен.')
        profile = await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1', tg)
        if required and not profile:
            raise web.HTTPConflict(reason='Сначала заполните профиль.')
        return pool, tg, profile

    async def profile_get(request):
        _, _, profile = await owner(request, False)
        return web.json_response({'profile': serial(profile) if profile else None})

    async def profile_put(request):
        pool, tg, _ = await owner(request, False)
        data = await body(request)
        if set(data) != {'display_name', 'timezone'}:
            raise ValueError('Укажите имя и часовой пояс.')
        name = clean_text(data['display_name'], 'имя', True, 200)
        zone = clean_text(data['timezone'], 'часовой пояс', True, 100)
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Неизвестный часовой пояс.')
        row = await pool.fetchrow('''INSERT INTO crm_personal_profiles(telegram_user_id,display_name,timezone)
          VALUES($1,$2,$3) ON CONFLICT(telegram_user_id) DO UPDATE SET display_name=excluded.display_name,
          timezone=excluded.timezone,updated_at=now() RETURNING *''', tg, name, zone)
        return web.json_response({'profile': serial(row)})

    async def projects(request):
        pool, _, profile = await owner(request)
        if request.method == 'POST':
            data = await body(request)
            if set(data) != {'title'}:
                raise ValueError('Укажите название проекта.')
            row = await pool.fetchrow('INSERT INTO crm_personal_projects(owner_id,title) VALUES($1,$2) RETURNING *',
                                      profile['id'], clean_text(data['title'], 'название', True, 300))
            return web.json_response({'project': serial(row)}, status=201)
        rows = await pool.fetch('SELECT * FROM crm_personal_projects WHERE owner_id=$1 ORDER BY id DESC', profile['id'])
        return web.json_response({'projects': [serial(r) for r in rows]})

    async def task_list(request):
        pool, _, profile = await owner(request)
        rows = await pool.fetch('SELECT * FROM crm_personal_tasks WHERE owner_id=$1 ORDER BY id DESC', profile['id'])
        return web.json_response({'tasks': [serial(r) for r in rows]})

    async def task_save(request):
        pool, _, profile = await owner(request)
        data = await body(request)
        creating = request.method == 'POST'
        values = parse_task(data, partial=not creating)
        if not creating and (type(data.get('version')) is not int or data['version'] < 1 or not values):
            raise ValueError('Укажите версию и изменения задачи.')
        if creating and 'version' in data:
            raise ValueError('Версия создаётся автоматически.')
        try:
            task_id = None if creating else int(request.match_info['id'])
            if task_id is not None and task_id <= 0: raise ValueError()
        except ValueError:
            raise web.HTTPNotFound(reason='Задача не найдена.')
        row = await save_task(pool, profile['id'], values, task_id, data.get('version'))
        return web.json_response({'task': serial(row)}, status=201 if creating else 200)

    async def history(request):
        pool, _, profile = await owner(request)
        try:
            task_id = int(request.match_info['id'])
        except ValueError:
            raise web.HTTPNotFound()
        if not await pool.fetchval('SELECT 1 FROM crm_personal_tasks WHERE id=$1 AND owner_id=$2', task_id, profile['id']):
            raise web.HTTPNotFound(reason='Задача не найдена.')
        rows = await pool.fetch('SELECT version,snapshot,created_at FROM crm_personal_task_history WHERE task_id=$1 AND owner_id=$2 ORDER BY version', task_id, profile['id'])
        return web.json_response({'history': [{**serial(r), 'snapshot': json.loads(r['snapshot']) if isinstance(r['snapshot'], str) else r['snapshot']} for r in rows]})

    return [web.get('/api/personal/profile', profile_get), web.put('/api/personal/profile', profile_put),
            web.get('/api/personal/projects', projects), web.post('/api/personal/projects', projects),
            web.get('/api/personal/tasks', task_list), web.post('/api/personal/tasks', task_save),
            web.patch('/api/personal/tasks/{id}', task_save), web.get('/api/personal/tasks/{id}/history', history)]
