"""Себестоимость звонка робота: три цены CRM считает сама, руками их не правят.

- Mango, рубли за минуту разговора: стоимость исходящих звонков за 30 дней из статистики Mango
  (поля стоимости в calls.raw_summary), делённая на минуты разговоров в этих звонках.
- ElevenLabs, рубли за 1000 символов: сумма следующего счёта подписки на лимит кредитов за тот же
  срок (GET /v1/user/subscription), умноженная на кредиты за символ у модели робота (GET /v1/models)
  и на курс ЦБ.
- YandexGPT, рубли за 1000 токенов: прайс Yandex AI Studio (синхронный режим, с НДС) для модели робота.

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
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

KEYS = ('mango_min', 'eleven_1k_chars', 'llm_1k_tokens')
EVERY_S = 6 * 3600
DAYS = 30
ELEVEN_API = 'https://api.elevenlabs.io/v1'
YANDEX_PRICING = 'https://yandex.cloud/ru/docs/ai-studio/pricing'
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


def call_money(raw: dict) -> float | None:
    """Стоимость звонка из статистики Mango: целиком или суммой по плечам."""
    for key in ('context_cost_full', 'cost'):
        value = num(raw.get(key))
        if value is not None:
            return value
    legs = [num(leg.get('cost')) for leg in raw.get('context_calls') or [] if isinstance(leg, dict)]
    legs = [v for v in legs if v is not None]
    return sum(legs) if legs else None


def mango_rate(raws: list[dict]) -> tuple[float, str]:
    money = minutes = 0.0
    priced = 0
    for raw in raws:
        cost = call_money(raw)
        if cost is None:
            continue
        priced += 1
        money += cost
        minutes += (num(raw.get('talk_duration')) or 0) / 60
    if not priced:
        raise PriceError('Mango не присылает стоимость звонков в статистике')
    if minutes < 1:
        raise PriceError('за 30 дней нет исходящих разговоров')
    return money / minutes, f'{ru(money, ".0f")} ₽ за {ru(minutes, ".0f")} мин исходящих разговоров за {DAYS} дней'


async def mango(pool) -> tuple[float, str]:
    rows = await pool.fetch(
        "SELECT raw_summary::jsonb AS r FROM calls WHERE call_started_at > now() - make_interval(days => $1) "
        "AND raw_summary IS NOT NULL AND raw_summary::jsonb->>'context_type' = '2'", DAYS)
    raws = [r['r'] if isinstance(r['r'], dict) else json.loads(r['r']) for r in rows]
    return mango_rate(raws)


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
    model = next((m for m in models if isinstance(m, dict) and m.get('model_id') == model_id), {})
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
    from . import robot  # noqa: F401  пути настроек робота задаются до импорта voice.speech
    from voice import speech  # модель синтеза со страницы «Голос»
    return eleven_rate(sub, models.json() if models.status_code == 200 else [], speech.eleven_params()[0],
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


async def yandex(http) -> tuple[float, str]:
    if os.getenv('VOICE_LLM', 'cvc').strip().lower() != 'yandex':
        raise PriceError('робот сейчас отвечает не через YandexGPT')
    name = yandex_name(os.getenv('VOICE_MODEL', 'yandexgpt-lite/latest'))
    return yandex_price((await yandex_page(http)).text, name)


async def mango_balance_probe(pool) -> str:
    """Отдаёт ли API Mango баланс счёта: только чтение, ничего не меняет."""
    import mango_client
    from crypto_util import decrypt
    client = await pool.fetchrow('SELECT vpbx_api_key_enc, vpbx_api_salt_enc FROM clients '
                                 'WHERE vpbx_api_key_enc IS NOT NULL ORDER BY id LIMIT 1')
    if not client:
        return 'no Mango key in clients'
    try:
        data = await mango_client.call('account/balance', decrypt(client['vpbx_api_key_enc']), decrypt(client['vpbx_api_salt_enc']), {})
        return json.dumps(data, ensure_ascii=False)[:300]
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
        if isinstance(res, BaseException):
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
    while True:
        try:
            await refresh(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('себестоимость робота: пересчёт не удался')
        await asyncio.sleep(EVERY_S)
