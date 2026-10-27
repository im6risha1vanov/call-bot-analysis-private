"""Себестоимость звонка робота: три цены CRM считает сама, руками их не правят.

- Mango, рубли за минуту разговора: списания с баланса счёта Mango (замер раз в час) на минуты
  исходящих разговоров, середина по дням за 30 дней (подробнее у mango_rate).
- ElevenLabs, рубли за 1000 символов: сумма следующего счёта подписки на лимит кредитов за тот же
  срок (GET /v1/user/subscription), умноженная на кредиты за символ у модели робота (GET /v1/models)
  и на курс ЦБ.
- YandexGPT, рубли за 1000 токенов: прайс Яндекс Облака из Billing API (ключ сервисного аккаунта
  yandex-sa-key.json, без него ключ робота); если не пустит, страница прайса Yandex AI Studio.

Пересчёт при старте CRM и раз в 6 часов. Если источник не ответил, остаётся прошлое значение и причина.
Коэффициент для клиента директор по-прежнему задаёт сам (robot_store.save_options).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

KEYS = ('mango_min', 'eleven_1k_chars', 'llm_1k_tokens')
EVERY_S = 6 * 3600
SNAP_S = 3600  # баланс Mango запоминается раз в час
MAX_GAP = timedelta(hours=2)  # если между замерами дольше, этот промежуток не считается
MIN_DAY_MINUTES = 10  # день с меньшим числом минут разговоров слишком шумный
BALANCE_KEY = 'mango_balance'
MSK = ZoneInfo('Europe/Moscow')
DAYS = 30
ELEVEN_API = 'https://api.elevenlabs.io/v1'
YANDEX_PRICING = 'https://aistudio.yandex.ru/docs/ru/ai-studio/pricing'
YANDEX_SKUS = 'https://billing.api.cloud.yandex.net/billing/v1/skus'
IAM_TOKENS = 'https://iam.api.cloud.yandex.net/iam/v1/tokens'
# Авторизованный ключ сервисного аккаунта без ролей: им CRM читает прайс Billing API (scripts/crm_setup.sh yandex-key).
SA_KEY_FILE = Path(__file__).resolve().parents[1] / 'yandex-sa-key.json'
# Строки прайса Billing API, которые не про обычную синхронную генерацию.
SKU_SKIP = re.compile(r'async|асинхрон|batch|пакетн|fine|дообуч|tun|lora|cach|кеш|кэш|tool|инструмент|embed|эмбед|classif|классиф', re.I)
CBR_DAILY = 'https://www.cbr.ru/scripts/XML_daily.asp'
CBR_MIRROR = 'https://www.cbr-xml-daily.ru/daily_json.js'  # те же курсы ЦБ, если cbr.ru не пускает сервер из-за границы
BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36',
           'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'ru-RU,ru;q=0.9'}
PERIOD_MONTHS = {'monthly_period': 1, '3_month_period': 3, '6_month_period': 6, 'annual_period': 12}
# Как модели из VOICE_MODEL называются в прайсе Яндекса.
YANDEX_NAMES = {'yandexgpt-5.1': 'YandexGPT Pro 5.1', 'yandexgpt': 'YandexGPT Pro 5', 'yandexgpt-lite': 'YandexGPT Lite'}


class PriceError(Exception):
    """Цену не посчитать: текст уходит на страницу настроек как причина."""


def ru(value: float, digits: str = 'g') -> str:
    """Число для подписи на странице: с запятой и пробелами между тысячами."""
    return format(value, ',' + digits if digits != 'g' else 'g').replace(',', '\u00a0').replace('.', ',')


def num(value) -> float | None:
    try:
        return float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Mango
# Стоимость звонков Mango в статистике не отдаёт, зато отдаёт баланс счёта. CRM раз в час запоминает
# баланс; за каждый час без пополнения списание делится на минуты исходящих разговоров этого часа.
# Часы складываются по дням, цена минуты — середина (медиана) дневных значений за 30 дней: так день
# абонентской платы не задирает цену, а часы с пополнением просто не считаются.


class NotYet(PriceError):
    """Данных пока мало: это не ошибка, цена появится сама."""


async def mango_keys(pool):
    from crypto_util import decrypt
    client = await pool.fetchrow('SELECT vpbx_api_key_enc, vpbx_api_salt_enc FROM clients '
                                 'WHERE vpbx_api_key_enc IS NOT NULL ORDER BY id LIMIT 1')
    if not client:
        raise PriceError('нет ключа API Mango')
    return decrypt(client['vpbx_api_key_enc']), decrypt(client['vpbx_api_salt_enc'])


async def mango_balance(pool) -> float:
    import mango_client
    data = await mango_client.call('account/balance', *await mango_keys(pool), {})
    value = num(data.get('balance'))
    if data.get('result') != 1000 or value is None:
        raise PriceError(f'Mango не отдал баланс (код {data.get("result")})')
    return value


async def snapshot(pool) -> None:
    """Запомнить баланс Mango сейчас; история хранится за 31 день."""
    from .robot_store import get_option, set_option
    value = await mango_balance(pool)
    now = datetime.now(timezone.utc)
    keep = (now - timedelta(days=DAYS + 1)).isoformat()
    history = [h for h in await get_option(pool, BALANCE_KEY, []) if h['at'] >= keep]
    history.append({'at': now.isoformat(timespec='seconds'), 'balance': value})
    await set_option(pool, BALANCE_KEY, history)


def mango_rate(history: list[dict], calls: list[tuple]) -> tuple[float, str]:
    """history: [{'at', 'balance'}] по времени; calls: [(начало звонка, секунды разговора)] исходящих."""
    points = sorted((datetime.fromisoformat(h['at']), float(h['balance'])) for h in history)
    calls = sorted(calls)
    days: dict = {}
    for (t0, b0), (t1, b1) in zip(points, points[1:]):
        if not timedelta(0) < t1 - t0 <= MAX_GAP or b1 > b0:  # сайт стоял или счёт пополнили
            continue
        talk = sum(s for at, s in calls if t0 <= at < t1)
        day = days.setdefault(t1.astimezone(MSK).date(), [0.0, 0.0])
        day[0] += b0 - b1
        day[1] += talk / 60
    rates = sorted(spent / minutes for spent, minutes in days.values() if minutes >= MIN_DAY_MINUTES)
    if not rates:
        raise NotYet('считается по списаниям с баланса Mango; первая цена появится после суток с разговорами')
    mid = len(rates) // 2
    value = rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2
    return value, f'списания с баланса Mango на минуту исходящих разговоров, середина по {len(rates)} дн.'


async def mango(pool) -> tuple[float, str]:
    from .robot_store import get_option
    rows = await pool.fetch(
        "SELECT call_started_at AS at, coalesce((raw_summary::jsonb->>'talk_duration')::numeric, 0) AS talk FROM calls "
        "WHERE call_started_at > now() - make_interval(days => $1) AND raw_summary IS NOT NULL "
        "AND raw_summary::jsonb->>'context_type' = '2'", DAYS + 1)
    return mango_rate(await get_option(pool, BALANCE_KEY, []), [(r['at'], float(r['talk'])) for r in rows])


# ------------------------------------------------------------------ курс ЦБ


def cbr_rate(xml: str, code: str) -> float:
    m = re.search(rf'<CharCode>{code.upper()}</CharCode>\s*<Nominal>(\d+)</Nominal>.*?<Value>([\d,.]+)</Value>', xml, re.S)
    if not m:
        raise PriceError(f'в курсах ЦБ нет {code.upper()}')
    return num(m.group(2)) / int(m.group(1))


def mirror_rate(data: dict, code: str) -> float:
    item = (data.get('Valute') or {}).get(code.upper()) or {}
    value, nominal = num(item.get('Value')), num(item.get('Nominal')) or 1
    if not value:
        raise PriceError(f'в курсах ЦБ нет {code.upper()}')
    return value / nominal


async def rub_per(http, code: str) -> float:
    if code.upper() == 'RUB':
        return 1.0
    try:
        r = await http.get(CBR_DAILY)
        r.raise_for_status()
        return cbr_rate(r.content.decode('cp1251', errors='replace'), code)
    except (httpx.HTTPError, PriceError) as e:
        log.info('курс ЦБ с cbr.ru не получен (%r), беру зеркало', e)
    r = await http.get(CBR_MIRROR)
    r.raise_for_status()
    return mirror_rate(r.json(), code)


# ------------------------------------------------------------------ ElevenLabs


def eleven_rate(sub: dict, models: list, model_id: str, rub: float) -> tuple[float, str]:
    invoice = sub.get('next_invoice') or {}
    cents = invoice.get('amount_due_cents') or invoice.get('subtotal_cents')
    limit = num(sub.get('character_limit'))
    if not cents or not limit:
        raise PriceError('в подписке ElevenLabs нет следующего счёта или лимита символов')
    # Счёт может быть за год, а лимит обновляется каждый месяц.
    bill = PERIOD_MONTHS.get(sub.get('billing_period'), 1)
    refresh = PERIOD_MONTHS.get(sub.get('character_refresh_period') or sub.get('billing_period'), 1)
    credits = limit * bill / refresh
    model = next((m for m in models if isinstance(m, dict) and m.get('model_id') == model_id), None)
    if model is None:  # без модели неизвестно, сколько кредитов стоит символ: 1.0 завысило бы цену вдвое
        raise PriceError(f'в списке моделей ElevenLabs нет {model_id}')
    rates = model.get('model_rates') or {}
    per_char = (num(rates.get('character_cost_multiplier')) or 1.0) * (num(rates.get('cost_discount_multiplier')) or 1.0)
    money = cents / 100
    cur = str(sub.get('currency') or 'usd').upper()
    value = money / credits * per_char * 1000 * rub
    return value, (f'тариф {sub.get("tier") or "?"}: {ru(money)} {cur} за {ru(credits, ".0f")} кредитов, '
                   f'{model_id} {ru(per_char)} кредита за символ, курс ЦБ {ru(rub, ".2f")} ₽')


async def eleven(http) -> tuple[float, str]:
    key = os.getenv('ELEVENLABS_API_KEY', '').strip()
    if not key:
        raise PriceError('нет ключа ElevenLabs')
    headers = {'xi-api-key': key}
    r = await http.get(ELEVEN_API + '/user/subscription', headers=headers)
    if r.status_code in (401, 403):
        raise PriceError('ключу ElevenLabs не хватает права User → Read')
    r.raise_for_status()
    sub = r.json()
    models = await http.get(ELEVEN_API + '/models', headers=headers)
    if models.status_code in (401, 403):
        raise PriceError('ключу ElevenLabs не хватает права Models → Read: без него неизвестно, сколько кредитов стоит символ')
    models.raise_for_status()
    # цена v4: от неё считаются наборы робота (v4 Turbo вдвое дешевле, у голоса Яндекса своя цена)
    return eleven_rate(sub, models.json(), 'eleven_v4',
                       await rub_per(http, str(sub.get('currency') or 'usd')))


# ------------------------------------------------------------------ YandexGPT


def yandex_name(model: str) -> str:
    part = next((p for p in model.split('/') if p.startswith('yandexgpt')), '')
    if part not in YANDEX_NAMES:
        raise PriceError(f'модели {model or "?"} нет в прайсе')
    return YANDEX_NAMES[part]


def page_text(page: str) -> str:
    """Текст страницы без разметки. Сайт документации Яндекса может прислать таблицу внутри JSON в скрипте,
    с экранированными <, > и ₽, поэтому сначала раскрываем экранирование, потом убираем теги."""
    page = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), page)
    page = page.replace('\\"', '"').replace('\\n', ' ').replace('\\/', '/')
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', page)))


def yandex_price(page: str, name: str) -> tuple[float, str]:
    """Цена за 1000 токенов из первой таблицы прайса с этой моделью (синхронный режим идёт первым)."""
    text = page_text(page)
    for m in re.finditer(re.escape(name).replace(r'\ ', r'\s*') + r'(?![\d.,])', text, re.I):
        tail = text[m.end():m.end() + 300]
        cut = tail.lower().find('yandexgpt')
        tail = tail[:cut] if cut >= 0 else tail
        if not re.match(r'[^\d₽]{0,40}\d', tail):
            continue
        prices = [num(p) for p in re.findall(r'(\d+(?:[.,]\d+)?)\s*₽', tail)]
        if not prices:
            continue
        first, last = prices[0], prices[-1]  # входящие и исходящие; токены робот считает вместе
        value = (first + last) / 2
        if not 0.01 <= value <= 50:
            raise PriceError(f'странная цена {value} ₽ в прайсе')
        same = first == last
        return value, f'прайс Яндекса, {name}: {ru(first)} ₽ вход{"" if same else f", {ru(last)} ₽ выход"} за 1000 токенов, с НДС'
    raise PriceError(f'в прайсе Яндекса не нашлась строка {name}')


async def yandex_page(http):
    # Без обычного браузерного заголовка Яндекс может ответить проверкой «вы не робот».
    r = await http.get(YANDEX_PRICING, follow_redirects=True, headers=BROWSER)
    r.raise_for_status()
    return r


def yandex_key() -> str:
    return (os.getenv('YANDEX_GPT_API_KEY') or os.getenv('YANDEX_TTS_API_KEY') or '').strip()


def sa_jwt(key: dict, now: int | None = None) -> str:
    """JWT ключа сервисного аккаунта Яндекс Облака (PS256) для обмена на IAM-токен."""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    now = int(now or time.time())
    head = b64(json.dumps({'typ': 'JWT', 'alg': 'PS256', 'kid': key['id']}).encode())
    body = b64(json.dumps({'aud': IAM_TOKENS, 'iss': key['service_account_id'], 'iat': now, 'exp': now + 3600}).encode())
    pem = key['private_key'][key['private_key'].index('-----BEGIN'):]  # в файле Яндекса перед ключом идёт строка-предупреждение
    private = serialization.load_pem_private_key(pem.encode(), password=None)
    sig = private.sign(f'{head}.{body}'.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    return f'{head}.{body}.{b64(sig)}'


async def yandex_auth(http) -> str:
    """Заголовок для Billing API: IAM-токен сервисного аккаунта, если его ключ лежит на сервере, иначе ключ робота."""
    path = Path(os.getenv('YANDEX_SA_KEY_FILE') or SA_KEY_FILE)
    if path.exists():
        r = await http.post(IAM_TOKENS, json={'jwt': sa_jwt(json.loads(path.read_text()))})
        if r.status_code >= 400:
            raise PriceError(f'Яндекс не выдал токен по ключу сервисного аккаунта (код {r.status_code})')
        return 'Bearer ' + r.json()['iamToken']
    key = yandex_key()
    if not key:
        raise PriceError('нет API-ключа Яндекса')
    return 'Api-Key ' + key


async def yandex_skus(http) -> list[dict]:
    """Прайс Яндекс Облака из Billing API (рубли)."""
    auth = await yandex_auth(http)
    skus, token = [], ''
    for _ in range(30):
        r = await http.get(YANDEX_SKUS, params={'currency': 'RUB', 'pageSize': 1000, **({'pageToken': token} if token else {})},
                           headers={'Authorization': auth})
        if r.status_code in (401, 403):
            who = 'сервисный аккаунт' if auth.startswith('Bearer') else 'ключ робота (нужен ключ сервисного аккаунта)'
            raise PriceError(f'Billing API Яндекса не пускает {who}, код {r.status_code}')
        r.raise_for_status()
        data = r.json()
        skus += data.get('skus') or []
        token = data.get('nextPageToken')
        if not token:
            break
    return skus


def sku_price(sku: dict, now: datetime | None = None) -> float | None:
    """Действующая цена SKU (первая ступень) за единицу pricingUnit."""
    now = now or datetime.now(timezone.utc)
    versions = [v for v in sku.get('pricingVersions') or []
                if datetime.fromisoformat(str(v.get('effectiveTime', '1970-01-01T00:00:00Z')).replace('Z', '+00:00')) <= now]
    if not versions:
        return None
    version = max(versions, key=lambda v: str(v.get('effectiveTime')))
    rates = [rate for e in version.get('pricingExpressions') or [] for rate in e.get('rates') or []]
    rates.sort(key=lambda rate: num(rate.get('startPricingQuantity')) or 0)
    return num(rates[0].get('unitPrice')) if rates else None


def per_1k_tokens(unit: str) -> float | None:
    """Во сколько раз умножить цену за pricingUnit, чтобы вышла цена за 1000 токенов."""
    unit = unit.lower().replace(' ', '')
    if 'token' not in unit and 'токен' not in unit:
        return None
    if unit.startswith(('1k', 'k', 'thousand', '1000', 'тыс')) or 'thousand' in unit:
        return 1.0
    if unit.startswith(('1m', 'million', 'млн')):
        return 0.001
    return 1000.0


def model_words(name: str) -> list[str]:
    return [w for w in name.lower().split() if w != 'yandexgpt']


def sku_matches(text: str, name: str) -> bool:
    text = text.lower()
    if 'gpt' not in text or SKU_SKIP.search(text):
        return False
    return all(re.search(rf'(?<![\w.]){re.escape(w)}(?![\w]|\.\d)', text) for w in model_words(name))


def yandex_sku_rate(skus: list[dict], name: str) -> tuple[float, str]:
    prices = []
    for sku in skus:
        text = f"{sku.get('name', '')} {sku.get('description', '')}"
        factor = per_1k_tokens(str(sku.get('pricingUnit', '')))
        price = sku_price(sku)
        if factor and price is not None and sku_matches(text, name):
            prices.append(price * factor)
    if not prices:
        raise PriceError(f'в прайсе Billing API нет строки {name}')
    first, last = min(prices), max(prices)  # вход и выход, если Яндекс считает их отдельно
    value = (first + last) / 2
    if not 0.01 <= value <= 50:
        raise PriceError(f'странная цена {value} ₽ в прайсе Billing API')
    return value, (f'прайс Яндекс Облака (Billing API), {name}: {ru(first)} ₽'
                   f'{"" if first == last else f" – {ru(last)} ₽"} за 1000 токенов')


async def yandex(http) -> tuple[float, str]:
    if os.getenv('VOICE_LLM', 'cvc').strip().lower() != 'yandex':
        raise PriceError('робот сейчас отвечает не через YandexGPT')
    from . import robot  # noqa: F401  пути настроек робота задаются до импорта voice
    from voice import presets
    model = presets.llm_model() or os.getenv('VOICE_MODEL', 'yandexgpt-lite/latest')
    name = yandex_name(model)
    try:
        return yandex_sku_rate(await yandex_skus(http), name)
    except (PriceError, httpx.HTTPError) as e:
        api = str(e) if isinstance(e, PriceError) else f'Billing API не ответил ({type(e).__name__})'
    try:  # запасной путь: страница прайса, если Яндекс её покажет без капчи
        return yandex_price((await yandex_page(http)).text, name)
    except (PriceError, httpx.HTTPError):
        raise PriceError(f'{api}; страница прайса Яндекса недоступна серверу (проверка «вы не робот»)') from None


async def mango_balance_probe(pool) -> str:
    """Отдаёт ли API Mango баланс счёта: только чтение, ничего не меняет."""
    try:
        return f'{ru(await mango_balance(pool), ".2f")} ₽'
    except Exception as e:  # noqa: BLE001 — это только отчёт
        return repr(e)[:300]


async def probe(pool, http=None) -> list[str]:
    """Для scripts/crm_setup.sh prices: что на самом деле пришло от источников. Без номеров и ключей."""
    out = []
    rows = await pool.fetch(
        "SELECT raw_summary::jsonb AS r FROM calls WHERE call_started_at > now() - make_interval(days => $1) "
        "AND raw_summary IS NOT NULL AND raw_summary::jsonb->>'context_type' = '2' LIMIT 500", DAYS)
    raws = [r['r'] if isinstance(r['r'], dict) else json.loads(r['r']) for r in rows]
    keys = sorted({k for raw in raws for k in raw})
    legs = sorted({k for raw in raws for leg in raw.get('context_calls') or [] if isinstance(leg, dict) for k in leg})
    out.append(f'mango: {len(raws)} outgoing calls; fields: {", ".join(keys)}; leg fields: {", ".join(legs)}')
    out.append('mango balance: ' + await mango_balance_probe(pool))
    async with http or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as http:
        key = os.getenv('ELEVENLABS_API_KEY', '').strip()
        if key:
            r = await http.get(ELEVEN_API + '/user/subscription', headers={'xi-api-key': key})
            detail = r.json().get('detail') if r.headers.get('content-type', '').startswith('application/json') else None
            out.append(f'elevenlabs: subscription answered {r.status_code} {json.dumps(detail, ensure_ascii=False)[:300] if detail else ""}')
            m = await http.get(ELEVEN_API + '/models', headers={'xi-api-key': key})
            ids = [x.get('model_id') for x in m.json() if isinstance(x, dict)] if m.status_code == 200 else []
            out.append(f'elevenlabs models: answered {m.status_code}; {", ".join(i for i in ids if i)[:300]}')
        try:
            gpt = [k for k in await yandex_skus(http) if 'gpt' in f"{k.get('name', '')} {k.get('description', '')}".lower()]
            out.append(f'yandex billing: {len(gpt)} gpt skus')
            out += [f"  {k.get('name')} | {k.get('pricingUnit')} | {sku_price(k)} | {str(k.get('description', ''))[:80]}" for k in gpt[:25]]
        except Exception as e:  # noqa: BLE001 — это только отчёт
            out.append(f'yandex billing: {e!r}'[:300])
        try:
            r = await yandex_page(http)
            text = page_text(r.text)
            name = yandex_name(os.getenv('VOICE_MODEL', 'yandexgpt-lite/latest'))
            at = text.lower().find(name.lower())
            out.append(f'yandex: {r.status_code} {r.url} {len(r.text)} bytes; yandexgpt x{text.lower().count("yandexgpt")}, '
                       f'rub x{text.count("₽")}, title: {text[:120]!r}; near model: {text[at:at + 200] if at >= 0 else "-"!r}')
        except Exception as e:  # noqa: BLE001 — это только отчёт
            out.append(f'yandex: {e!r}')
    return out


# ------------------------------------------------------------------ пересчёт


async def status(pool) -> dict:
    from .robot_store import get_option
    return await get_option(pool, 'prices_auto', {})


async def refresh(pool, http=None) -> dict:
    """Пересчитать три цены. Неудачная цена остаётся прежней, рядом пишется причина."""
    from .robot_store import set_option
    old = await status(pool)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    own = http is None
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10))
    try:
        jobs = {'mango_min': mango(pool), 'eleven_1k_chars': eleven(http), 'llm_1k_tokens': yandex(http)}
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
    finally:
        if own:
            await http.aclose()
    out = {}
    for key, res in got.items():
        prev = old.get(key) or {}
        if isinstance(res, NotYet):
            out[key] = {'value': prev.get('value'), 'at': prev.get('at'), 'note': prev.get('note') or str(res), 'error': None, 'tried': now}
        elif isinstance(res, BaseException):
            why = str(res) if isinstance(res, PriceError) else f'источник не ответил ({type(res).__name__})'
            if not isinstance(res, PriceError):
                log.warning('себестоимость робота %s: %r', key, res)
            out[key] = {'value': prev.get('value'), 'at': prev.get('at'), 'note': prev.get('note', ''), 'error': why, 'tried': now}
        else:
            value, note = res
            out[key] = {'value': round(value, 4), 'at': now, 'note': note, 'error': None, 'tried': now}
    await set_option(pool, 'prices_auto', out)
    return out


async def loop(pool) -> None:
    """Раз в час баланс Mango, раз в 6 часов пересчёт цен (и сразу при старте CRM)."""
    last = None
    while True:
        try:
            await snapshot(pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — цена Mango просто дольше не обновится
            log.warning('баланс Mango не получен: %r', e)
        if last is None or time.monotonic() - last >= EVERY_S - 60:
            last = time.monotonic()
            try:
                await refresh(pool)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('себестоимость робота: пересчёт не удался')
        await asyncio.sleep(SNAP_S)
