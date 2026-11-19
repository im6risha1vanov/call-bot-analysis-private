"""Private day-plan foundation. No calendar delivery or scheduling occurs here."""
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from aiohttp import web
from tools import Forbidden
from . import personal, team_assignments


def local_day(value, zone):
    return value.astimezone(zone).date() if value else None


def day_bounds_utc(day, zone):
    """Half-open UTC interval for a local calendar day, including DST changes."""
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return start, end


def parse_day(value):
    try:
        if not isinstance(value, str): raise ValueError()
        return date.fromisoformat(value)
    except (ValueError, TypeError): raise ValueError('Укажите дату в формате ГГГГ-ММ-ДД.')


def settings_data(data):
    fields = {'work_start', 'work_end', 'priorities', 'morning_time', 'evening_time', 'version'}
    if not isinstance(data, dict) or set(data) != fields or type(data.get('version')) is not int or data['version'] < 0:
        raise ValueError('Укажите настройки и версию.')
    out = {}
    for key in ('work_start', 'work_end', 'morning_time', 'evening_time'):
        value = data[key]
        if value is None and key in ('morning_time', 'evening_time'):
            out[key] = None; continue
        try:
            if not isinstance(value, str) or len(value) != 5: raise ValueError()
            parsed = time.fromisoformat(value)
            if parsed.tzinfo is not None or parsed.second or parsed.microsecond: raise ValueError()
        except (ValueError, TypeError): raise ValueError('Укажите время в формате ЧЧ:ММ.')
        out[key] = parsed
    if out['work_start'] >= out['work_end']: raise ValueError('Конец рабочего дня должен быть позже начала.')
    priorities = data['priorities']
    if not isinstance(priorities, list) or len(priorities) > 5: raise ValueError('Укажите до пяти приоритетов.')
    out['priorities'] = [personal.clean_text(x, 'приоритет', True, 300) for x in priorities]
    return out


def show_settings(row):
    if row is None:
        return {'work_start':'09:00','work_end':'18:00','priorities':[], 'morning_time':None,'evening_time':None,'version':0}
    result = {k: row[k] for k in ('work_start','work_end','priorities','morning_time','evening_time','version')}
    for key in ('work_start','work_end','morning_time','evening_time'):
        if result[key] is not None: result[key] = result[key].strftime('%H:%M')
    if isinstance(result['priorities'], str): result['priorities'] = json.loads(result['priorities'])
    return result


def classify(item, day, zone, kind):
    due = local_day(item.get('due_at'), zone)
    planned = item.get('planned_day') if kind == 'personal' else None
    if item.get('blocker'): return 'blocked'
    if due is not None and due < day: return 'overdue'
    if planned == day or due == day: return 'today'
    if planned is None and due is None: return 'unscheduled'
    return None


async def sections(db, ident, profile, zone, day):
    private = await db.fetch("SELECT * FROM crm_personal_tasks WHERE owner_id=$1 AND status NOT IN ('done','cancelled') ORDER BY id DESC", profile['id'])
    team = await db.fetch("SELECT * FROM crm_team_assignments WHERE company_id=$1 AND assignee_id=$2 AND status NOT IN ('accepted','cancelled') ORDER BY id DESC", *ident)
    groups = {'today': [], 'overdue': [], 'blocked': [], 'unscheduled': []}
    for kind, rows in (('personal', private), ('team', team)):
        for row in rows:
            group = classify(row, day, zone, kind)
            if group:
                item = personal.serial(row) if kind == 'personal' else await team_assignments.visible(db, ident, team_assignments.serial(row))
                groups[group].append({'kind':kind, **item})
    return {'date':day.isoformat(), 'timezone':profile['timezone'], **groups,
            'appointments':None, 'appointments_status':'not_connected'}


def routes(viewer, body, pool_key):
    async def context(request):
        pool = request.app[pool_key]; who = await viewer(request)
        ident = await team_assignments.identity(pool, who)
        profile = await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1', ident[1])
        if not profile: raise web.HTTPConflict(reason='Сначала заполните профиль.')
        try: zone = ZoneInfo(profile['timezone'])
        except (ZoneInfoNotFoundError, ValueError): raise web.HTTPConflict(reason='Проверьте часовой пояс профиля.')
        return pool, ident, profile, zone
    async def get_settings(request):
        pool, _, profile, _ = await context(request)
        row = await pool.fetchrow('SELECT * FROM crm_personal_day_settings WHERE owner_id=$1', profile['id'])
        return web.json_response({'settings': show_settings(row), 'timezone':profile['timezone']})
    async def put_settings(request):
        pool, _, profile, _ = await context(request); data = await body(request); values = settings_data(data)
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('SELECT id FROM crm_personal_profiles WHERE id=$1 FOR UPDATE', profile['id'])
            current = await conn.fetchrow('SELECT * FROM crm_personal_day_settings WHERE owner_id=$1', profile['id'])
            if (current['version'] if current else 0) != data['version']:
                raise web.HTTPConflict(reason='Настройки уже изменены.')
            row = await conn.fetchrow('''INSERT INTO crm_personal_day_settings(owner_id,work_start,work_end,priorities,morning_time,evening_time)
              VALUES($1,$2,$3,$4::jsonb,$5,$6) ON CONFLICT(owner_id) DO UPDATE SET work_start=excluded.work_start,
              work_end=excluded.work_end,priorities=excluded.priorities,morning_time=excluded.morning_time,
              evening_time=excluded.evening_time,version=crm_personal_day_settings.version+1,updated_at=now() RETURNING *''',
              profile['id'], values['work_start'], values['work_end'], json.dumps(values['priorities'], ensure_ascii=False), values['morning_time'], values['evening_time'])
        return web.json_response({'settings':show_settings(row),'timezone':profile['timezone']})
    async def plan(request):
        pool, ident, profile, zone = await context(request)
        day = parse_day(request.query['date']) if 'date' in request.query else datetime.now(zone).date()
        return web.json_response(await sections(pool, ident, profile, zone, day))

    return [web.get('/api/personal/day', plan), web.get('/api/personal/day/settings', get_settings),
            web.put('/api/personal/day/settings', put_settings)]
