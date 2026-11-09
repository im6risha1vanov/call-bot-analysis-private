"""Учёт расходов CRM и чтение остатков у провайдеров для директора."""
from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

import httpx
from cryptography.fernet import InvalidToken

import mango_client
from crypto_util import decrypt
from tools import home_client_id
from . import company, robot_store, store

PERIODS = (7, 30, 90)
PROVIDER_TIMEOUT = 5
MANGO_DOC = ('https://developers.mango-office.ru/support/5_api-i-razrabotka/1_api-mango-office/'
             '5_opisanie_metodov_api_virtualnoy_ats_mango/7_7_api_konfiguratsiya/3_7_6_poluchenie_balansa/')
ELEVEN_DOC = 'https://elevenlabs.io/docs/api-reference/user/subscription/get'
OPENROUTER_DOC = 'https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key'
DEEPGRAM_DOC = 'https://developers.deepgram.com/reference/manage/billing/list'
DADATA_DOC = 'https://dadata.ru/api/balance/'

UNIT_LABELS = ('Разборы звонков и проверка промтов', 'Методология и оценка тренировки', 'Варианты разбора звонков')
EXPENSES = (
    ('AveronGPT', 'RUB', 'crm_questions', 'cost_rub', 'created_at', 'Расчёт CRM по использованию модели'),
    ('Агент доноров', 'RUB', 'donor_runs', 'rub', 'created_at', 'Расчёт CRM по запускам, включая поиск'),
    ('Тренажёр', 'USD', 'training_sessions', 'cost_usd', 'started_at', 'Расчёт CRM по дате начала тренировки'),
)
SHARED_CREDENTIALS = (
    ('Astra / StarIMG', ('CVC_API_KEY',)),
    ('Claude / Anthropic', ('ANTHROPIC_API_KEY',)),
    ('Octav API', ('OCTAV_API_KEY',)),
    ('YandexGPT', ('YANDEX_GPT_API_KEY', 'YANDEX_TTS_API_KEY')),
    ('Yandex SpeechKit TTS', ('YANDEX_TTS_API_KEY',)),
    ('Yandex SpeechKit STT', ('YANDEX_STT_API_KEY', 'YANDEX_TTS_API_KEY')),
    ('Yandex Search', ('YANDEX_SEARCH_API_KEY', 'YANDEX_GPT_API_KEY')),
    ('Telegram Bot API', ('BOT_TOKEN',)),
    ('Telegram-бот тренажёра', ('TRAIN_BOT_TOKEN',)),
    ('Telegram API для рабочих чатов', ('TG_API_HASH',)),
    ('Google Sheets', ('GOOGLE_SERVICE_ACCOUNT_FILE',)),
)


def number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def configured(*names: str) -> str:
    return next((value for name in names if (value := os.getenv(name, '').strip())), '')


def provider(name, enabled, *, scope='account', source=None, note=None) -> dict:
    return {'name': name, 'scope': scope, 'configured': bool(enabled), 'status': 'unavailable' if enabled else 'missing',
            'metrics': [], 'source': source, 'note': note}


def metric(name: str, amount: float, unit: str) -> dict:
    return {'name': name, 'amount': amount, 'unit': unit}


def measured(item: dict, metrics: list[dict]) -> dict:
    return {**item, 'status': 'verified', 'metrics': metrics, 'at': datetime.now(timezone.utc).isoformat()}


async def mango(pool, client_id: int) -> dict:
    row = await pool.fetchrow(
        'SELECT vpbx_api_key_enc, vpbx_api_salt_enc FROM clients WHERE id=$1', client_id)
    enabled = row and row['vpbx_api_key_enc'] and row['vpbx_api_salt_enc']
    item = provider('Mango Office', enabled, scope='company', source=MANGO_DOC,
                    note='Баланс ВАТС. Для чтения нужен доступ к API Mango.')
    if not enabled:
        return item
    try:
        data = await asyncio.wait_for(mango_client.call(
            'account/balance', decrypt(row['vpbx_api_key_enc']), decrypt(row['vpbx_api_salt_enc']), {},
            timeout=PROVIDER_TIMEOUT), PROVIDER_TIMEOUT)
        amount, currency = number(data.get('balance')), data.get('currency')
        if data.get('result') == 1000 and amount is not None and currency in ('RUB', 'USD', 'EUR', 'KZT'):
            return measured(item, [metric('Баланс ВАТС', amount, currency)])
    except (ValueError, TypeError, AttributeError, KeyError, InvalidToken, RuntimeError, TimeoutError, httpx.HTTPError):
        # Тело ответа и поля запроса могут содержать реквизиты. Отдаём только статус.
        pass
    return item


async def get_json(url: str, headers: dict) -> dict:
    async def fetch():
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=False, trust_env=False) as http:
            response = await http.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    try:
        return await asyncio.wait_for(fetch(), PROVIDER_TIMEOUT)
    except (httpx.HTTPError, ValueError, TimeoutError):
        return {}


async def eleven() -> dict:
    key = configured('ELEVENLABS_API_KEY')
    item = provider('ElevenLabs', key, source=ELEVEN_DOC, note='Квота подписки. Для чтения нужно право User → Read.')
    if not key:
        return item
    data = await get_json('https://api.elevenlabs.io/v1/user/subscription', {'xi-api-key': key})
    used, limit = number(data.get('character_count')), number(data.get('character_limit'))
    if used is None or limit is None or used < 0 or limit < 0:
        return item
    return measured(item, [metric('Остаток квоты', max(0, limit - used), 'credits'),
                           metric('Использовано', used, 'credits'), metric('Лимит', limit, 'credits')])


async def openrouter() -> dict:
    key = configured('OPENROUTER_API_KEY')
    item = provider('OpenRouter', key, source=OPENROUTER_DOC,
                    note='Расход и лимит этого ключа. Общий баланс счёта требует отдельного доступа к биллингу.')
    if not key:
        return item
    data = await get_json('https://openrouter.ai/api/v1/key', {'Authorization': 'Bearer ' + key})
    key_data = data.get('data')
    if not isinstance(key_data, dict):
        return item
    fields = (('Расход за месяц', 'usage_monthly'), ('Остаток лимита ключа', 'limit_remaining'), ('Лимит ключа', 'limit'))
    metrics = [metric(label, value, 'USD') for label, field in fields if (value := number(key_data.get(field))) is not None]
    return measured(item, metrics) if metrics else item


async def deepgram() -> dict:
    key = configured('DEEPGRAM_API_KEY')
    item = provider('Deepgram', key, source=DEEPGRAM_DOC,
                    note='Баланс проекта. Нужны ID проекта и право ключа на чтение биллинга.')
    if not key:
        return item
    try:
        project = UUID(configured('DEEPGRAM_PROJECT_ID'))
    except ValueError:
        return item
    data = await get_json(f'https://api.deepgram.com/v1/projects/{project}/balances', {'Authorization': 'Token ' + key})
    balances = data.get('balances')
    if not isinstance(balances, list):
        return item
    metrics = []
    for balance in balances:
        if isinstance(balance, dict) and balance.get('units') == 'USD':
            amount = number(balance.get('amount'))
            if amount is not None:
                metrics.append(metric('Баланс проекта', amount, 'USD'))
    return measured(item, metrics) if metrics else item


async def dadata() -> dict:
    key, secret = configured('DADATA_KEY'), configured('DADATA_SECRET')
    item = provider('DaData', key, source=DADATA_DOC,
                    note='Баланс пользователя. Для чтения нужен секретный ключ биллинга.')
    if not key or not secret:
        return item
    data = await get_json('https://dadata.ru/api/v2/profile/balance', {
        'Authorization': 'Token ' + key, 'X-Secret': secret,
        'Accept': 'application/json', 'Content-Type': 'application/json',
    })
    balance = number(data.get('balance'))
    return measured(item, [metric('Баланс', balance, 'RUB')]) if balance is not None else item


async def expense_rows(pool, client_id: int, first, today, start, end) -> list[dict]:
    expenses = []
    for table, label in zip(company.UNIT_TABLES, UNIT_LABELS, strict=True):
        if await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', table):
            units = await pool.fetchval(f'SELECT sum(spent_units) FROM {table} WHERE client_id=$1 AND day BETWEEN $2 AND $3',
                                        client_id, first, today)
            amount = number(units)
            if amount is not None:
                amount = number(amount * company.rub_per_unit())
            expenses.append({'name': label, 'amount': amount, 'unit': 'RUB', 'note': 'Оценка по кредит-единицам'})

    if await pool.fetchval("SELECT to_regclass('rop_agent_daily_spend') IS NOT NULL"):
        amount = await pool.fetchval('SELECT sum(spent_usd) FROM rop_agent_daily_spend WHERE client_id=$1 AND day BETWEEN $2 AND $3',
                                     client_id, first, today)
        expenses.append({'name': 'Агент РОПа', 'amount': number(amount), 'unit': 'USD', 'note': 'Учёт расхода в CRM'})

    for label, unit, table, column, when, note in EXPENSES:
        if await pool.fetchval(
            'SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass($1) AND attname=$2 AND NOT attisdropped)',
            table, column,
        ):
            amount = await pool.fetchval(
                f'SELECT sum({column}) FROM {table} WHERE client_id=$1 AND {when} >= $2 AND {when} < $3',
                client_id, start, end)
            expenses.append({'name': label, 'amount': number(amount), 'unit': unit, 'note': note})
    return expenses


async def robot_expense(pool, start, end) -> dict | None:
    if not await pool.fetchval("SELECT to_regclass('robot_calls') IS NOT NULL AND to_regclass('robot_options') IS NOT NULL"):
        return None
    usage = await pool.fetchrow(
        """SELECT count(*) AS calls, coalesce(sum(tts_chars), 0) AS chars, coalesce(sum(llm_tokens), 0) AS tokens,
                  coalesce(sum(ceil(greatest(duration_s, 0) / 15.0)), 0) AS asr_units
           FROM robot_calls WHERE owner !~ '^#[0-9]+$' AND at >= $1 AND at < $2""", start, end)
    amount = None
    if usage['calls']:
        prices = await robot_store.prices(pool)
        synth, llm = prices['tts_1k_chars'], prices['llm_1k_tokens']
        if (synth or not usage['chars']) and (llm or not usage['tokens']):
            amount = number(usage['chars'] / 1000 * synth + usage['tokens'] / 1000 * llm +
                            float(usage['asr_units']) * robot_store.ASR_15S)
    return {'name': 'Голосовой робот', 'amount': amount, 'unit': 'RUB',
            'note': 'Оценка по текущим тарифам синтеза, распознавания и модели.'}


async def overview(pool, client_id: int, days: int = 30) -> dict:
    if days not in PERIODS:
        raise ValueError('Выберите период 7, 30 или 90 дней.')
    tz = await store.zone(pool, client_id)
    today = datetime.now(tz).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, time.min, tz)
    end = datetime.combine(today + timedelta(days=1), time.min, tz)
    expenses = await expense_rows(pool, client_id, first, today, start, end)
    limit = number(await company.ai_limit_rub(pool, client_id))
    today_rub = number(await company.spent_today_rub(pool, client_id))
    # Mango хранится в записи компании. Общие ключи из .env видны только домашней компании.
    if client_id == await home_client_id(pool):
        robot, *providers = await asyncio.gather(
            robot_expense(pool, start, end), mango(pool, client_id), eleven(), openrouter(), deepgram(), dadata())
        if robot is not None:
            expenses.append(robot)
        providers.extend(provider(name, configured(*names), note='Отдельные данные биллинга не подключены.')
                         for name, names in SHARED_CREDENTIALS)
    else:
        providers = [await mango(pool, client_id)]
    return {'from': first.isoformat(), 'to': today.isoformat(), 'days': days, 'expenses': expenses,
            'today_rub': today_rub, 'ai_limit_rub': limit, 'providers': providers}
