"""Учёт расходов CRM и чтение остатков у провайдеров для директора."""
from __future__ import annotations

import asyncio
import math
import os
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from cryptography.fernet import InvalidToken

import mango_client
from crypto_util import decrypt
from tools import home_client_id
from . import company, robot_prices, robot_store, store

PERIODS = (7, 30, 90)
PROVIDER_TIMEOUT = 5
MANGO_DOC = ('https://developers.mango-office.ru/support/5_api-i-razrabotka/1_api-mango-office/'
             '5_opisanie_metodov_api_virtualnoy_ats_mango/7_7_api_konfiguratsiya/3_7_6_poluchenie_balansa/')
ELEVEN_DOC = 'https://elevenlabs.io/docs/api-reference/user/subscription/get'
DEEPGRAM_DOC = 'https://developers.deepgram.com/reference/manage/billing/list'
STARIMG_SITE = 'https://ai.starimg.ru/'
YANDEX_DOC = 'https://yandex.cloud/ru/docs/billing/api-ref/BillingAccount/get'
EXPENSES = (
    ('AveronGPT', 'RUB', 'crm_questions', 'cost_rub', 'created_at', 'Расчёт CRM по использованию модели'),
    ('Агент доноров', 'RUB', 'donor_runs', 'rub', 'created_at', 'Расчёт CRM по запускам, включая поиск'),
    ('Тренажёр', 'USD', 'training_sessions', 'cost_usd', 'started_at', 'Расчёт CRM по дате начала тренировки'),
)
GUIDES = {
    'Mango Office': ('Телефония CRM, записи и история звонков, исходящие звонки робота через ВАТС.',
                     'Исходящие могут остановиться. Для импорта истории и записей отдельно нужен действующий API-коннектор.',
                     'Проверьте, что в ВАТС включён API-коннектор и серверу CRM разрешён доступ к запросу баланса.'),
    'Deepgram': ('Расшифровка звонков и распознавание речи в голосовых сценариях с Deepgram.',
                 'Новые аудиозаписи не будут расшифровываться через Deepgram, а зависящие от них разборы задержатся.',
                 'Нужен ID рабочего проекта Deepgram. Если действующий ключ не читает баланс, нужен отдельный ключ с правом billing:read.'),
    'ElevenLabs': ('Синтез речи для голосов ElevenLabs в голосовом роботе.',
                   'Робот не сможет озвучивать реплики выбранными голосами ElevenLabs.',
                   'Для автоматического чтения квоты действующему ключу нужен доступ User → Read.'),
    'AI Star': ('Разборы звонков и переписок, агент РОПа, тренажёр и модели Starimg в AveronGPT и поиске доноров.',
                'Остановятся запросы на модели AI Star.',
                'Остаток доступен в кабинете AI Star.'),
    'Шлюз разборов': ('Разборы звонков, проверка промтов и оценка тренировки.',
                       'Остановятся запросы к настроенному шлюзу разборов.',
                       'Нужны данные из кабинета поставщика о способе чтения остатка по API-ключу.'),
    'Yandex Cloud': ('YandexGPT, синтез и распознавание речи SpeechKit, поиск Yandex Search.',
                     'Эти функции могут остановиться, если облако потеряет доступ к платёжному аккаунту.',
                     'Укажите ID платёжного аккаунта и выдайте существующему сервисному аккаунту роль billing.accounts.viewer на нём.'),
}


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
    uses, impact, setup = GUIDES[name]
    return {'name': name, 'scope': scope, 'configured': bool(enabled), 'status': 'unavailable' if enabled else 'missing',
            'metrics': [], 'source': source, 'note': note, 'uses': uses, 'impact': impact, 'setup': setup}


def metric(name: str, amount: float, unit: str) -> dict:
    return {'name': name, 'amount': amount, 'unit': unit}


def measured(item: dict, metrics: list[dict], funding=None) -> dict:
    return {**item, 'status': 'verified', 'metrics': metrics, 'funding': funding,
            'at': datetime.now(timezone.utc).isoformat()}


def available(amount: float) -> str:
    return 'depleted' if amount <= 0 else 'available'


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
            return measured(item, [metric('Баланс ВАТС', amount, currency)], available(amount))
    except mango_client.MangoHTTPError as exc:
        if exc.status in (401, 403):
            item['note'] = f'Mango отклонил доступ к балансу (HTTP {exc.status}).'
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
    remaining = max(0, limit - used)
    return measured(item, [metric('Остаток квоты', remaining, 'credits'),
                           metric('Использовано', used, 'credits'), metric('Лимит', limit, 'credits')], available(remaining))


async def deepgram() -> dict:
    key = configured('DEEPGRAM_BILLING_API_KEY', 'DEEPGRAM_API_KEY')
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
    amounts = [amount for balance in balances if isinstance(balance, dict) and balance.get('units') == 'USD'
               and (amount := number(balance.get('amount'))) is not None]
    if not amounts:
        return item
    remaining = sum(amounts)
    return measured(item, [metric('Баланс проекта', remaining, 'USD')], available(remaining))


def analysis_gateway() -> str:
    try:
        url = urlsplit(configured('CVC_BASE_URL') or 'https://ai.starimg.ru/v1')
    except ValueError:
        return 'Шлюз разборов'
    return 'AI Star' if url.hostname in {'ai.starimg.ru', 'ai.starimg.space'} else 'Шлюз разборов'


async def analysis_provider() -> dict:
    name = analysis_gateway()
    return provider(name, configured('CVC_API_KEY'), source=STARIMG_SITE if name == 'AI Star' else None)


async def yandex() -> dict:
    billing_id = configured('YANDEX_BILLING_ACCOUNT_ID')
    enabled = configured('YANDEX_GPT_API_KEY', 'YANDEX_TTS_API_KEY', 'YANDEX_STT_API_KEY', 'YANDEX_SEARCH_API_KEY')
    item = provider('Yandex Cloud', enabled or billing_id, source=YANDEX_DOC,
                    note='Один платёжный аккаунт для облачных функций проекта.')
    if not re.fullmatch(r'[a-z0-9]{1,64}', billing_id):
        return item
    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT):
            async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=False, trust_env=False) as http:
                auth = await robot_prices.yandex_auth(http)
                if not auth.startswith('Bearer '):
                    return item
                response = await http.get(f'https://billing.api.cloud.yandex.net/billing/v1/billingAccounts/{billing_id}',
                                          headers={'Authorization': auth})
                response.raise_for_status()
                data = response.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, AttributeError, TimeoutError, robot_prices.PriceError):
        return item
    if not isinstance(data, dict) or data.get('id') != billing_id:
        return item
    amount, currency = number(data.get('balance')), data.get('currency')
    if amount is None or currency not in ('RUB', 'USD', 'KZT'):
        return item
    funding = 'inactive' if data.get('active') is False else ('available' if amount > 0 else 'check')
    return measured(item, [metric('Баланс платёжного аккаунта', amount, currency)], funding)


async def expense_rows(pool, client_id: int, first, today, start, end) -> list[dict]:
    expenses = []
    gateway_units = []
    for table in company.UNIT_TABLES:
        if await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', table):
            units = await pool.fetchval(f'SELECT sum(spent_units) FROM {table} WHERE client_id=$1 AND day BETWEEN $2 AND $3',
                                        client_id, first, today)
            if (amount := number(units)) is not None:
                gateway_units.append(amount)
    if gateway_units:
        expenses.append({'name': analysis_gateway(), 'amount': number(math.fsum(gateway_units) * company.rub_per_unit()),
                         'unit': 'RUB', 'note': 'Общая оценка по учёту токенов'})

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


def expense_totals(expenses: list[dict]) -> list[dict]:
    return [{'unit': unit, 'amount': math.fsum(row['amount'] for row in expenses
                                              if row['unit'] == unit and row['amount'] is not None)}
            for unit in sorted({row['unit'] for row in expenses if row['amount'] is not None})]


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
            robot_expense(pool, start, end), mango(pool, client_id), analysis_provider(), yandex(), deepgram(), eleven())
        if robot is not None:
            expenses.append(robot)
    else:
        providers = [await mango(pool, client_id)]
    return {'from': first.isoformat(), 'to': today.isoformat(), 'days': days, 'expenses': expenses,
            'totals': expense_totals(expenses),
            'today_rub': today_rub, 'ai_limit_rub': limit, 'providers': providers}
