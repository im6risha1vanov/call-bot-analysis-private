"""Вкладка «Компании»: мы (платформа) заводим компании, включаем модули и смотрим расход.

Открыта только нашим учётным записям с отметкой platform (crm_setup.sh platform <логин>). Компания —
строка clients плюс её настройки (crm/company.py) и учётка директора. Вход в компанию для поддержки —
только просмотр, каждое действие пишется в platform_log.
"""
from __future__ import annotations

import json
from datetime import timedelta
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import crypto_util
import mango_client
from tools import Forbidden, home_client_id

from . import accounts, auth, company

SUPPORT_TTL = timedelta(hours=4)
SETTING_KEYS = ('callcenter_sheet', 'contract_email', 'ai_day_limit_rub')


async def require(pool, who) -> None:
    """Только наши учётки с отметкой platform, и только из нашей компании."""
    if who is None or not who.platform or who.support or who.company != await home_client_id(pool):
        raise Forbidden('Раздел недоступен.')


async def log(pool, who, client_id: int | None, action: str, detail: dict | None = None) -> None:
    await pool.execute('INSERT INTO platform_log(invite_id,client_id,action,detail) VALUES($1,$2,$3,$4::jsonb)',
                       who.invite_id if who else None, client_id, action, json.dumps(detail or {}, ensure_ascii=False))


async def companies(pool) -> list[dict]:
    rows = await pool.fetch(
        """SELECT c.id, c.timezone, c.processing_enabled, coalesce(c.vpbx_api_key_enc::text, '') <> '' AS mango,
                  (SELECT count(*) FROM employees e WHERE e.client_id=c.id AND e.role='manager') AS managers,
                  (SELECT count(*) FROM calls k WHERE k.client_id=c.id AND k.call_started_at > now() - interval '7 days') AS calls_7d,
                  (SELECT count(*) FROM crm_invites i WHERE i.client_id=c.id AND i.login IS NOT NULL AND i.revoked_at IS NULL) AS logins,
                  (SELECT max(s.last_seen_at) FROM crm_sessions s WHERE s.client_id=c.id AND s.support_by IS NULL) AS last_seen
           FROM clients c ORDER BY c.id""")
    out = []
    for r in rows:
        info = await company.info(pool, r['id'])
        out.append({'id': r['id'], 'name': info['name'], 'home': info['home'], 'modules': info['modules'],
                    'settings': {k: info['settings'].get(k) for k in SETTING_KEYS}, 'timezone': r['timezone'],
                    'mango': r['mango'], 'working': r['processing_enabled'], 'managers': r['managers'],
                    'calls_7d': r['calls_7d'], 'logins': r['logins'],
                    'last_seen': r['last_seen'].isoformat() if r['last_seen'] else None,
                    'ai_today': round(await company.spent_today_rub(pool, r['id']), 2),
                    'ai_limit': await company.ai_limit_rub(pool, r['id'])})
    return out


def clean_name(value) -> str:
    name = ' '.join(str(value or '').split())
    if not name or len(name) > 80:
        raise ValueError('Название компании: от 1 до 80 символов.')
    return name


def clean_modules(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError('Неверный список модулей.')
    return [m for m in company.MODULES if m in value]


def clean_settings(value) -> dict:
    value = value if isinstance(value, dict) else {}
    out = {}
    sheet = str(value.get('callcenter_sheet') or '').strip()
    if sheet:
        if '/d/' in sheet:  # вставили ссылку на таблицу целиком
            sheet = sheet.split('/d/', 1)[1].split('/', 1)[0]
        if not sheet.replace('-', '').replace('_', '').isalnum() or len(sheet) > 100:
            raise ValueError('Не похоже на ссылку на Google Таблицу.')
        out['callcenter_sheet'] = sheet
    email = str(value.get('contract_email') or '').strip()
    if email:
        if '@' not in email or len(email) > 120:
            raise ValueError('Неверный e-mail для договоров.')
        out['contract_email'] = email
    limit = str(value.get('ai_day_limit_rub') if value.get('ai_day_limit_rub') is not None else '').strip().replace(',', '.')
    if limit:
        try:
            number = float(limit)
        except ValueError:
            raise ValueError('Лимит на ИИ — число рублей в день.')
        if not 0 <= number <= 100000:
            raise ValueError('Лимит на ИИ — от 0 до 100 000 ₽ в день.')
        out['ai_day_limit_rub'] = number
    return out


async def _free_column_values(conn) -> dict:
    """Обязательные поля clients без значения по умолчанию: заполняем нейтральным, чтобы строка вставилась
    на любой версии схемы бота."""
    rows = await conn.fetch(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_name='clients' AND is_nullable='NO' AND column_default IS NULL
             AND column_name NOT IN ('id','timezone','processing_enabled')""")
    neutral = {'text': '', 'character varying': '', 'integer': 0, 'bigint': 0, 'smallint': 0, 'numeric': 0,
               'double precision': 0, 'real': 0, 'boolean': False, 'jsonb': '{}', 'json': '{}', 'ARRAY': '{}'}
    out = {}
    for r in rows:
        if r['data_type'] not in neutral:
            raise ValueError(f'Не знаю, чем заполнить поле clients.{r["column_name"]} ({r["data_type"]}).')
        out[r['column_name']] = neutral[r['data_type']]
    return out


async def create(pool, who, data: dict) -> dict:
    """Новая компания: строка clients, настройки, учётка директора. Отдел работает, когда подключим Манго."""
    name = clean_name(data.get('name'))
    tz = str(data.get('timezone') or 'Europe/Moscow')
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Неизвестный часовой пояс.')
    modules = clean_modules(data.get('modules', list(company.DEFAULT_MODULES)))
    settings = clean_settings(data.get('settings'))
    director = ' '.join(str(data.get('director') or '').split())[:80] or 'Директор'
    login = str(data.get('login') or '').strip() or await accounts.free_login(pool, accounts.slug(name) or 'company')
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('LOCK TABLE clients IN EXCLUSIVE MODE')
        client_id = await conn.fetchval('SELECT coalesce(max(id), 0) + 1 FROM clients')
        extra = await _free_column_values(conn)
        cols = ['id', 'timezone', 'processing_enabled', *extra]
        await conn.execute(f'INSERT INTO clients({",".join(cols)}) VALUES({",".join(f"${i + 1}" for i in range(len(cols)))})',
                           client_id, tz, False, *extra.values())
        await conn.execute('INSERT INTO company_settings(client_id,name,modules,settings) VALUES($1,$2,$3,$4::jsonb)',
                           client_id, name, modules, json.dumps(settings))
    try:
        password = await auth.account_create(pool, client_id, 'director', login, name=director,
                                             password=data.get('password') or None)
    except Exception:
        await pool.execute('DELETE FROM clients WHERE id=$1', client_id)  # без директора компания не нужна
        raise
    await log(pool, who, client_id, 'create', {'name': name, 'modules': modules, 'login': login})
    return {'id': client_id, 'login': auth.clean_login(login), 'password': password}


async def update(pool, who, client_id: int, data: dict) -> dict:
    if not await pool.fetchval('SELECT 1 FROM clients WHERE id=$1', client_id):
        raise ValueError('Такой компании нет.')
    current = await company.info(pool, client_id)
    name = clean_name(data.get('name', current['name']))
    modules = clean_modules(data.get('modules', current['modules']))
    settings = clean_settings(data.get('settings', {k: current['settings'].get(k) for k in SETTING_KEYS}))
    await pool.execute(
        """INSERT INTO company_settings(client_id,name,modules,settings,updated_at) VALUES($1,$2,$3,$4::jsonb,now())
           ON CONFLICT(client_id) DO UPDATE SET name=EXCLUDED.name, modules=EXCLUDED.modules, settings=EXCLUDED.settings,
             updated_at=now()""", client_id, name, modules, json.dumps(settings))
    await log(pool, who, client_id, 'update', {'name': name, 'modules': modules, 'settings': settings})
    return next(c for c in await companies(pool) if c['id'] == client_id)


async def connect_mango(pool, who, client_id: int, data: dict, check=None) -> dict:
    """Ключи ВАТС Манго компании: сначала проверяем запросом баланса, потом сохраняем и включаем отдел."""
    if client_id == await home_client_id(pool):
        raise ValueError('Ключи нашей Манго меняются на сервере, не здесь.')
    if not await pool.fetchval('SELECT 1 FROM clients WHERE id=$1', client_id):
        raise ValueError('Такой компании нет.')
    key, salt = str(data.get('key') or '').strip(), str(data.get('salt') or '').strip()
    if not key or not salt or len(key) > 200 or len(salt) > 200:
        raise ValueError('Нужны уникальный код ВАТС и ключ для подписи из личного кабинета Манго.')
    check = check or (lambda k, s: mango_client.call('account/balance', k, s, {}))
    try:
        answer = await check(key, salt)
    except Exception as exc:  # неверные ключи, выключенный API
        raise ValueError(f'Манго не приняла ключи: {str(exc)[:160]}')
    await pool.execute('UPDATE clients SET vpbx_api_key_enc=$2, vpbx_api_salt_enc=$3, processing_enabled=true WHERE id=$1',
                       client_id, crypto_util.encrypt(key), crypto_util.encrypt(salt))
    await log(pool, who, client_id, 'mango', {'balance': (answer or {}).get('balance')})
    return {'balance': (answer or {}).get('balance')}


async def support_session(pool, who, client_id: int) -> str:
    """Сессия директора компании только для просмотра; к своей сессии вернёт /support-exit."""
    if client_id == await home_client_id(pool) or not await pool.fetchval('SELECT 1 FROM clients WHERE id=$1', client_id):
        raise ValueError('Такой компании нет.')
    session = secrets.token_urlsafe(32)
    await pool.execute(
        """INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,is_test,support_by)
           VALUES($1,now()+$2,'director',$3,true,$4)""", auth.digest(session), SUPPORT_TTL, client_id, who.invite_id)
    await log(pool, who, client_id, 'support')
    return session
