"""Company configuration and account operations for the platform owner."""
import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from tools import Forbidden, home_client_id
from . import admin_permissions, auth, company, platform


ROLES = ('director', 'manager', 'client')


def as_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def check_version(data, current):
    if type(data.get('version')) is not int or data['version'] != current:
        raise web.HTTPConflict(reason='Данные изменились. Обновите страницу.')


async def require(pool, who):
    invite = os.getenv('CRM_PLATFORM_ADMIN_INVITE_ID', '')
    if (not invite.isascii() or not invite.isdecimal() or who.role != 'director'
            or who.test or who.support or who.support_by is not None or who.invite_id != int(invite)
            or who.company != await home_client_id(pool)):
        raise Forbidden('Админка платформы недоступна.')


async def client(pool, client_id, *, lock=False):
    row = await pool.fetchrow(
        '''SELECT id, timezone, processing_enabled,
                  coalesce(vpbx_api_key_enc::text, '') <> ''
                    AND coalesce(vpbx_api_salt_enc::text, '') <> '' AS mango
           FROM clients WHERE id=$1''' + (' FOR NO KEY UPDATE' if lock else ''), client_id)
    if row is None:
        raise web.HTTPNotFound(reason='Компания не найдена.')
    return dict(row)


async def audit(conn, who, client_id, resource, entity, action, before=None, after=None):
    await conn.execute(
        '''INSERT INTO crm_admin_audit(client_id,actor_id,resource,entity,action,before_state,after_state)
           VALUES($1,$2,$3,$4,$5,$6,$7)''', client_id, who.invite_id, resource, str(entity), action,
        as_json(before) if before is not None else None, as_json(after) if after is not None else None)


async def history(pool, client_id, before, resource):
    rows = await pool.fetch(
        '''SELECT * FROM crm_admin_audit WHERE client_id=$1 AND ($2::bigint IS NULL OR id < $2)
               AND ($3::text IS NULL OR resource=$3) ORDER BY id DESC LIMIT 101''', client_id, before, resource)
    items = [{**dict(r), 'created_at': r['created_at'].isoformat(),
              'before_state': decoded(r['before_state']), 'after_state': decoded(r['after_state'])}
             for r in rows[:100]]
    return {'items': items, 'next_before': items[-1]['id'] if len(rows) > 100 else None}


def modules(value):
    if not isinstance(value, list) or any(not isinstance(m, str) or m not in company.MODULES for m in value):
        raise ValueError('Выберите модули из каталога.')
    return list(dict.fromkeys(value))


def settings(value):
    if not isinstance(value, dict) or value.keys() - set(platform.SETTING_KEYS):
        raise ValueError('Выберите параметры из каталога.')
    return platform.clean_settings(value)


async def configuration(pool, client_id):
    row = await pool.fetchrow(
        '''SELECT c.timezone,s.name,s.modules,s.settings,s.admin_version FROM clients c
           LEFT JOIN company_settings s ON s.client_id=c.id WHERE c.id=$1''', client_id)
    if row is None:
        raise web.HTTPNotFound(reason='Компания не найдена.')
    home = client_id == await home_client_id(pool)
    known = decoded(row['settings']) or {}
    return {'version': row['admin_version'] or 0, 'timezone': row['timezone'] or 'Europe/Moscow',
            'name': row['name'] or ('Averon' if home else f'Компания {client_id}'),
            'modules': (list(company.MODULES if home else company.DEFAULT_MODULES)
                        if row['modules'] is None else [m for m in row['modules'] if m in company.MODULES]),
            'settings': {k: known[k] for k in platform.SETTING_KEYS if k in known}}


async def detail(pool, client_id):
    status = await client(pool, client_id)
    config = await configuration(pool, client_id)
    effective = (await company.info(pool, client_id))['settings']
    sources = {}
    for key in platform.SETTING_KEYS:
        stored = config['settings'].get(key)
        sources[key] = ('company' if stored not in (None, '') else
                        'environment' if effective.get(key) else 'default')
    directors = [a for a in await auth.invites(pool, client_id) if a['role'] == 'director']
    return {**status, **config, 'directors': directors, 'settings_sources': sources,
            'effective_settings': {k: effective.get(k) for k in platform.SETTING_KEYS},
            'ai_spent_today_rub': round(await company.spent_today_rub(pool, client_id), 2)}


async def create_company(conn, who, data):
    data = dict(data)
    data['password'] = auth.clean_password(data.get('password'))
    if 'modules' in data:
        data['modules'] = modules(data['modules'])
    if 'settings' in data:
        data['settings'] = settings(data['settings'])
    result = await platform.create_company(conn, who, data)
    client_id = result['id']
    config = await configuration(conn, client_id)
    await audit(conn, who, client_id, 'settings', client_id, 'create', after=config)
    return {'id': client_id, 'login': result['login'], **config}


async def update_company(conn, who, client_id, data, *, restore=None):
    await client(conn, client_id, lock=True)
    await conn.execute(
        '''INSERT INTO company_settings(client_id) VALUES($1) ON CONFLICT (client_id) DO NOTHING''', client_id)
    raw = await conn.fetchval('SELECT settings FROM company_settings WHERE client_id=$1 FOR UPDATE', client_id)
    before = await configuration(conn, client_id)
    check_version(data, before['version'])
    patch = restore if restore is not None else data
    name = platform.clean_name(patch.get('name', before['name']))
    zone = patch.get('timezone', before['timezone'])
    if not isinstance(zone, str):
        raise ValueError('Недопустимый часовой пояс.')
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError('Неизвестный часовой пояс.')
    enabled = modules(patch.get('modules', before['modules']))
    store = decoded(raw) or {}
    if restore is not None:
        store = {k: v for k, v in store.items() if k not in platform.SETTING_KEYS}
    if 'settings' in patch:
        clean = settings(patch['settings'])
        for key in patch['settings']:
            if key in clean:
                store[key] = clean[key]
            else:
                store.pop(key, None)
    await conn.execute('UPDATE clients SET timezone=$2 WHERE id=$1', client_id, zone)
    await conn.execute(
        '''UPDATE company_settings SET name=$2,modules=$3,settings=$4,updated_at=now() WHERE client_id=$1''',
        client_id, name, enabled, as_json(store))
    after = await configuration(conn, client_id)
    await audit(conn, who, client_id, 'settings', client_id, 'restore' if restore else 'update', before, after)
    return after


async def restore_settings(conn, who, client_id, entry_id, data):
    row = await conn.fetchrow(
        '''SELECT after_state FROM crm_admin_audit WHERE client_id=$1 AND id=$2 AND resource='settings' ''',
        client_id, entry_id)
    if row is None or row['after_state'] is None:
        raise web.HTTPNotFound(reason='Версия настроек не найдена.')
    return await update_company(conn, who, client_id, data, restore=decoded(row['after_state']))


async def permissions(pool, client_id, role):
    if role not in ROLES:
        raise web.HTTPNotFound(reason='Роль не найдена.')
    row = await pool.fetchrow(
        'SELECT denied,version FROM crm_admin_role_rules WHERE client_id=$1 AND role=$2', client_id, role)
    return {'role': role, 'denied': row['denied'] if row else [], 'version': row['version'] if row else 0}


async def update_permissions(conn, who, client_id, role, data):
    await client(conn, client_id, lock=True)
    before = await permissions(conn, client_id, role)
    check_version(data, before['version'])
    choices = {f'{s}.{a}' for s in admin_permissions.SECTIONS for a in admin_permissions.ACTIONS}
    values = data.get('denied')
    if not isinstance(values, list) or any(not isinstance(p, str) or p not in choices for p in values):
        raise ValueError('Выберите права из каталога.')
    await conn.execute(
        '''INSERT INTO crm_admin_role_rules(client_id,role,denied) VALUES($1,$2,$3)
           ON CONFLICT (client_id,role) DO UPDATE
           SET denied=excluded.denied,version=crm_admin_role_rules.version+1''',
        client_id, role, sorted(set(values)))
    after = await permissions(conn, client_id, role)
    await audit(conn, who, client_id, 'permissions', role, 'update', before, after)
    return after


async def account(conn, client_id, invite_id, *, lock=False):
    row = await conn.fetchrow(
        '''SELECT id,role,login,extension,client_key,name,platform,revoked_at IS NOT NULL AS disabled
           FROM crm_invites WHERE client_id=$1 AND id=$2''' + (' FOR UPDATE' if lock else ''),
        client_id, invite_id)
    if row is None:
        raise web.HTTPNotFound(reason='Учётная запись не найдена.')
    return dict(row)


async def create_account(conn, who, client_id, data):
    role = data.get('role')
    if role not in ROLES:
        raise ValueError('Выберите роль.')
    login = auth.clean_login(data.get('login'))
    await auth.account_create(conn, client_id, role, login, data.get('extension'), data.get('client_key'),
                              data.get('name'), password=auth.clean_password(data.get('password')))
    target = await conn.fetchval('SELECT id FROM crm_invites WHERE client_id=$1 AND login=$2 AND revoked_at IS NULL',
                                 client_id, login)
    item = await account(conn, client_id, target)
    await audit(conn, who, client_id, 'accounts', target, 'create', after=item)
    return item


async def change_account(conn, who, client_id, target, action, data):
    if target == who.invite_id:
        raise Forbidden('Используйте настройки своего профиля.')
    before = await account(conn, client_id, target, lock=True)
    if before['disabled'] and action != 'sessions':
        raise web.HTTPConflict(reason='Учётная запись отключена.')
    if action == 'disable':
        await auth.invite_revoke(conn, client_id, target)
    elif action == 'password':
        password = auth.hash_password(auth.clean_password(data.get('password')))
        if not before['login']:
            raise ValueError('Учётной записи нужен логин.')
        await conn.execute(
            'UPDATE crm_invites SET password_hash=$2,password_enc=NULL WHERE id=$1', target, password)
    elif action == 'role':
        if before['platform']:
            raise Forbidden('Сначала снимите роль оператора платформы.')
        role = data.get('role')
        if role not in ROLES:
            raise ValueError('Выберите роль.')
        ext, customer = await auth.check_target(conn, client_id, role, data.get('extension'), data.get('client_key'))
        await conn.execute(
            'UPDATE crm_invites SET role=$2,extension=$3,client_key=$4,telegram_user_id=NULL WHERE id=$1',
            target, role, ext, customer)
    await conn.execute('DELETE FROM crm_sessions WHERE invite_id=$1 OR support_by=$1', target)
    after = await account(conn, client_id, target)
    await audit(conn, who, client_id, 'accounts', target, action, before, after)
    return after
