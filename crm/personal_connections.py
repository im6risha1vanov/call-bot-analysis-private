"""Private connection drafts; no provider authorization or credentials."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from aiohttp import web
from crm import personal
from tools import Forbidden

PROVIDERS = {'yandex', 'google', 'microsoft', 'other'}
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


def validate(kind, data):
    if kind not in ('mail', 'calendar'):
        raise web.HTTPNotFound()
    fields = {'provider', 'email', 'timezone', 'version', 'display_name' if kind == 'mail' else 'calendar_name'}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError('Неверные поля настроек.')
    provider = data['provider']
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError('Выберите сервис.')
    email = personal.clean_text(data['email'], 'адрес', True, 254)
    if not EMAIL.fullmatch(email) or '..' in email:
        raise ValueError('Укажите корректный адрес почты.')
    name_key = 'display_name' if kind == 'mail' else 'calendar_name'
    name = personal.clean_text(data[name_key], 'название', False, 200)
    timezone = personal.clean_text(data['timezone'], 'часовой пояс', False, 100)
    if timezone:
        try: ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError): raise ValueError('Неизвестный часовой пояс.')
    version = data['version']
    if type(version) is not int or version < 0:
        raise ValueError('Неверная версия настроек.')
    return provider, email, name, timezone, version


def routes(viewer, body, pool_key):
    async def owner(request):
        pool = request.app[pool_key]; who = await viewer(request)
        tg = await personal.identity(pool, who)
        if tg is None: raise Forbidden('Личный раздел недоступен.')
        if not await pool.fetchval('SELECT 1 FROM employees WHERE telegram_user_id=$1 AND client_id=$2 AND archived_at IS NULL', tg, who.actor.client_id):
            raise Forbidden('Личный раздел недоступен.')
        if not await pool.fetchval('SELECT 1 FROM crm_personal_profiles WHERE telegram_user_id=$1', tg):
            raise web.HTTPConflict(reason='Сначала заполните профиль.')
        return pool, tg, who.actor.client_id

    async def get(request):
        pool, tg, client = await owner(request)
        rows = await pool.fetch('SELECT kind,provider,email,display_name,calendar_name,timezone,version,updated_at FROM crm_personal_connections WHERE telegram_user_id=$1 AND client_id=$2', tg, client)
        return web.json_response({'connections': {r['kind']: personal.serial(r) for r in rows}})

    async def put(request):
        pool, tg, client = await owner(request)
        kind = request.match_info['kind']; provider, email, name, zone, version = validate(kind, await body(request))
        display, calendar = (name, '') if kind == 'mail' else ('', name)
        if version == 0:
            row = await pool.fetchrow('''INSERT INTO crm_personal_connections(telegram_user_id,client_id,kind,provider,email,display_name,calendar_name,timezone)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING RETURNING *''', tg, client, kind, provider, email, display, calendar, zone)
        else:
            row = await pool.fetchrow('''UPDATE crm_personal_connections SET provider=$4,email=$5,display_name=$6,calendar_name=$7,timezone=$8,version=version+1,updated_at=now()
                WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3 AND version=$9 RETURNING *''', tg, client, kind, provider, email, display, calendar, zone, version)
        if not row: raise web.HTTPConflict(reason='Настройки изменились. Обновите раздел перед сохранением.')
        result = personal.serial(row)
        result.pop('telegram_user_id'); result.pop('client_id')
        return web.json_response({'connection': result})
    return [web.get('/api/personal/connections', get), web.put('/api/personal/connections/{kind}', put)]
