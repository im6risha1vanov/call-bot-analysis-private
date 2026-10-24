"""Настройки компании: модули и то, что раньше было одним на сервер в .env.

Компания — строка clients (у неё свои ключи Манго, часовой пояс, сотрудники). Здесь её название,
включённые модули и настройки вроде таблицы колл-центра. У нашей компании (tools.home_client_id) без
своей строки всё берётся из .env, как до многоклиентности: выкатка ничего не меняет.
"""
from __future__ import annotations

import json
import os

from tools import Forbidden, home_client_id

# Модули, которые включаются компании отдельно. Отдел продаж (сводка, звонки, перезвоны, сделки,
# сотрудники, отчёты) есть у всех.
MODULES = {
    'callcenter': 'Колл-центр',
    'robot': 'Робот',
    'contracts': 'Договоры',
    'sources': 'Источники',
    'presentations': 'Презентации',
    'gpt': 'AveronGPT',
}
DEFAULT_MODULES = ('sources', 'gpt')  # новой компании сразу; остальное включаем, когда настроено

# Настройка → переменная .env, из которой её берёт наша компания, пока не задана в базе.
SETTINGS = {
    'callcenter_sheet': 'KITCHEN_SHEET_ID',  # таблица колл-центра («Прозвон лидов»)
    'contract_email': 'CONTRACT_OUR_EMAIL',  # e-mail в реквизитах договора
}

# Адреса API, закрытые без модуля. Проверяет web.py в одном месте для всех ролей.
API_MODULES = (
    ('/api/callcenter', 'callcenter'), ('/api/cabinet', 'callcenter'),
    ('/api/robot', 'robot'),
    ('/api/contracts', 'contracts'),
    ('/api/sources', 'sources'),
    ('/api/presentations', 'presentations'),
    ('/api/gpt', 'gpt'),
)


async def info(pool, client_id: int) -> dict:
    """{'id', 'name', 'home', 'modules': [...], 'settings': {...}} компании."""
    home = client_id == await home_client_id(pool)
    row = await pool.fetchrow('SELECT name, modules, settings FROM company_settings WHERE client_id=$1', client_id) \
        if await _has_table(pool) else None
    raw = row['settings'] if row else None
    settings = dict(json.loads(raw) if isinstance(raw, str) else raw or {})
    if home:
        for key, env in SETTINGS.items():
            if not settings.get(key) and os.getenv(env):
                settings[key] = os.getenv(env)
    if row and row['modules'] is not None:
        modules = [m for m in row['modules'] if m in MODULES]
    else:
        modules = list(MODULES) if home else list(DEFAULT_MODULES)
    name = (row['name'] if row else None) or ('Averon' if home else f'Компания {client_id}')
    return {'id': client_id, 'name': name, 'home': home, 'modules': modules, 'settings': settings}


async def has_module(pool, client_id: int | None, module: str) -> bool:
    return client_id is not None and module in (await info(pool, client_id))['modules']


async def require(pool, client_id: int | None, module: str) -> None:
    if not await has_module(pool, client_id, module):
        raise Forbidden(f'Модуль «{MODULES[module]}» не подключён.')


async def setting(pool, client_id: int | None, key: str) -> str | None:
    if client_id is None:
        return None
    return (await info(pool, client_id))['settings'].get(key) or None


def module_of(path: str) -> str | None:
    return next((m for prefix, m in API_MODULES if path == prefix or path.startswith(prefix + '/')), None)


async def _has_table(pool) -> bool:
    return bool(await pool.fetchval("SELECT to_regclass('company_settings') IS NOT NULL"))


# ------------------------------------------------------------------ расход на ИИ

# Разборы, память, презентации и агент РОПа копят расход в «единицах» провайдера (astra_daily_spend и др.):
# (свежий вход + кеш·0,1 + выход) × множитель модели. Перевод в рубли приблизительный, сверять с кабинетом
# провайдера: AI_RUB_PER_MILLION_UNITS, по умолчанию 50 ₽ (≈ $3,5 за миллион токенов при множителе 6,5).
UNIT_TABLES = ('astra_daily_spend', 'methodology_daily_spend', 'automatic_call_spend')


def rub_per_unit() -> float:
    return float(os.getenv('AI_RUB_PER_MILLION_UNITS', '50')) / 1e6


async def spent_today_rub(pool, client_id: int) -> float:
    """Расход компании на ИИ за сегодня (по её часовому поясу), ₽: разборы и отчёты, AveronGPT, тренажёр."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow')
    day = datetime.now(tz).date()
    units = 0.0
    for table in UNIT_TABLES:
        if await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', table):
            units += float(await pool.fetchval(f'SELECT coalesce(sum(spent_units),0) FROM {table} WHERE client_id=$1 AND day=$2',
                                               client_id, day) or 0)
    rub = units * rub_per_unit()
    if await pool.fetchval("SELECT to_regclass('crm_questions') IS NOT NULL") and \
            await pool.fetchval("SELECT 1 FROM information_schema.columns WHERE table_name='crm_questions' AND column_name='cost_rub'"):
        rub += float(await pool.fetchval(
            "SELECT coalesce(sum(cost_rub),0) FROM crm_questions WHERE client_id=$1 AND (created_at AT TIME ZONE $2)::date=$3",
            client_id, str(tz), day) or 0)
    return rub


async def ai_limit_rub(pool, client_id: int) -> float | None:
    """Дневной лимит компании на ИИ, ₽ (настройка ai_day_limit_rub); None — без лимита."""
    value = await setting(pool, client_id, 'ai_day_limit_rub')
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


async def ai_over_limit(pool, client_id: int | None) -> bool:
    if client_id is None:
        return False
    limit = await ai_limit_rub(pool, client_id)
    return limit is not None and await spent_today_rub(pool, client_id) >= limit
