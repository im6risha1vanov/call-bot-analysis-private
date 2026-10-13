"""Прозвон лидов по скрипту «Кухонные гарнитуры»: итоги попыток на отдельных листах.

У оператора (KITCHEN_EXTENSIONS) свой скрипт и своя таблица «Прозвон лидов»,
которую ведут руками (листы «Сводка», «По дням», «Кухонные гарнитуры»). Бот
заводит в этой таблице три собственных листа в том же оформлении и пишет только
в них: «<менеджер> · Звонки» (строка на лида, попытки «Звонок 1/2/3» с датами,
сумма по тарифам, собранные данные), «<менеджер> · Сводка» и «<менеджер> · По дням».
Лист базы бот только читает (id, проект и дата базы по телефону). Листы людей он
не очищает, не оформляет и не пишет в них: каждый запрос на изменение
проверяется на sheetId своих листов (own_only).

Недозвон (звонок без разговора) записывается как НДЗ без запроса к модели;
разговор разбирается промтом review/kitchen_prompt.md. «Заинтересованный»
оплачивается дороже остальных, поэтому код ставит его только при полном
чек-листе из памятки оператора, иначе понижает до «Перезвон» с пометкой,
чего не хватает.

Настройки: KITCHEN_EXTENSIONS (добавочные через запятую), KITCHEN_SHEET_ID,
KITCHEN_MANAGER (имя в названиях листов), KITCHEN_LEADS_TAB (лист с базой,
только чтение), KITCHEN_PRICES (первичный, повторный, заинтересованный, ₽),
KITCHEN_REGIONS (регионы доставки через запятую; пусто — регион не проверяется),
KITCHEN_SINCE (ISO-время, с которого недозвоны пишутся в таблицу).
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .engine import norm
from . import sheets

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).with_name('kitchen_prompt.md').read_text(encoding='utf-8')
          .split('\n---\n', 1)[-1].strip())
MAX_TOKENS = int(os.getenv('KITCHEN_MAX_TOKENS', '3000'))
STATUSES = ('ndz', 'callback', 'not_relevant', 'interested')
LABELS = {'callback': 'Перезвон', 'not_relevant': 'Неактуально', 'interested': 'Заинтересованный'}
NDZ_LABELS = ('НДЗ', 'НДЗ 2', 'НДЗ 3')
STARTED = datetime.now(timezone.utc)


def extensions() -> set[str]:
    return {e.strip() for e in os.getenv('KITCHEN_EXTENSIONS', '').split(',') if e.strip()}


def handles(extension) -> bool:
    return str(extension or '') in extensions()


def regions() -> list[str]:
    return [norm(r) for r in re.split(r'[,;\n]', os.getenv('KITCHEN_REGIONS', '')) if norm(r)]


def sheet_enabled() -> bool:
    return bool(extensions() and os.getenv('KITCHEN_SHEET_ID') and os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE'))


def since() -> datetime:
    raw = os.getenv('KITCHEN_SINCE', '').strip()
    try:
        value = datetime.fromisoformat(raw)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except ValueError:
        return STARTED


def region_ok(city) -> bool | None:
    allowed = regions()
    if not allowed:
        return None
    city = norm(city)
    return bool(city) and any(r in city or city in r for r in allowed)


def decide(data: dict, transcript: str) -> dict:
    """Согласует ответ модели с чек-листом «лид готов». Ничего не дописывает от себя."""
    data = dict(data or {})
    facts = data.get('facts') if isinstance(data.get('facts'), dict) else {}
    status = data.get('status') if data.get('status') in STATUSES else 'callback'
    if not (transcript or '').strip():
        status = 'ndz'
    comment = str(data.get('comment') or '').strip()
    if status == 'interested':
        missing = [label for label, ok in (
            ('сроки', facts.get('timeline') == 'да'), ('город', bool(facts.get('city'))),
            ('плёнка', facts.get('film_ok') is True), ('бюджет от 250 тыс.', facts.get('budget_ok') is True),
            ('имя', bool(facts.get('name'))), ('время звонка', bool(facts.get('callback_time')))) if not ok]
        if facts.get('city') and region_ok(facts['city']) is False:
            missing.append(f"регион «{facts['city']}» не в списке")
        if missing:
            status = 'callback'
            comment = f"Проверь: не хватает {', '.join(missing)}. {comment}".strip()
    data.update(status=status, facts=facts, comment=comment[:240])
    return data


def ndz(reason: str) -> dict:
    return {'status': 'ndz', 'last_step': 0, 'facts': {}, 'comment': reason}


async def run(transcript: str) -> tuple[dict, float]:
    if not (transcript or '').strip():
        return ndz('Нет разговора'), 0.0
    from analysis import _call
    data, cost = await _call(PROMPT, 'РАСШИФРОВКА:\n' + transcript, MAX_TOKENS)
    return decide(data, transcript), cost


def level(data: dict) -> None:
    """Попытки прозвона не входят в статистику звонков Averon (✅/⚠️/❌ в astra_analysis)."""
    return None


async def record(conn, call, data: dict, cost: float = 0.0) -> None:
    await conn.execute(
        """INSERT INTO lead_attempts(call_id, client_id, extension, client_number, call_started_at, status, comment, data, cost_units)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
           ON CONFLICT(call_id) DO UPDATE SET status=EXCLUDED.status, comment=EXCLUDED.comment, data=EXCLUDED.data,
             cost_units=EXCLUDED.cost_units
           WHERE lead_attempts.written_at IS NULL""",
        call['id'], call['client_id'], call['extension'], call['client_number'], call['call_started_at'],
        data['status'], data.get('comment'), json.dumps(data, ensure_ascii=False), cost)


async def record_missed(pool) -> int:
    """Исходящие звонки оператора без разговора — НДЗ (в разбор они не попадают)."""
    exts = sorted(extensions())
    if not exts:
        return 0
    rows = await pool.fetch(
        """SELECT c.* FROM calls c
           WHERE c.extension = ANY($1::text[]) AND c.status='skipped' AND c.call_started_at >= $2
             AND (c.raw_summary::jsonb->>'context_type') = '2' AND coalesce((c.raw_summary::jsonb->>'talk_duration')::numeric, 0) = 0
             AND NOT EXISTS (SELECT 1 FROM lead_attempts a WHERE a.call_id=c.id)""", exts, since())
    for call in rows:
        async with pool.acquire() as conn:
            await record(conn, call, ndz('Не дозвонился'))
    return len(rows)


def digits(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r'\D', '', str(value or ''))[-10:]


MANAGER = os.getenv('KITCHEN_MANAGER', 'Ольга')
CALLS_TAB, SUMMARY_TAB, DAYS_TAB = (f'{MANAGER} · {name}' for name in ('Звонки', 'Сводка', 'По дням'))
LEADS_TAB = os.getenv('KITCHEN_LEADS_TAB', 'Кухонные гарнитуры')
SYNC_MINUTES = 10
SLOTS = 3  # в таблице людей три попытки на лида, сумма считается по ним
HEADER = ['Телефон', 'id', 'Проект', 'Дата базы', 'Напоминание', 'Комментарий', 'Звонок 1', 'Звонок 2', 'Звонок 3',
          'Сумма, ₽', 'Дата звонка 1', 'Дата звонка 2', 'Дата звонка 3', 'Город', 'Материал', 'Бюджет', 'Имя',
          'Удобное время']
WIDTHS = [120, 95, 230, 90, 140, 360, 120, 120, 120, 75, 110, 110, 110, 120, 90, 100, 150, 140]
CHIPS = {  # как чипы в листе базы: НДЗ серо-голубой, Неактуально красный, Заинтересованный зелёный
    'НДЗ': ('#DDE4EE', '#3C4A5C'), 'НДЗ 2': ('#DDE4EE', '#3C4A5C'), 'НДЗ 3': ('#DDE4EE', '#3C4A5C'),
    'Перезвон': ('#FFF2CC', '#7F6000'), 'Неактуально': ('#F4CCCC', '#990000'), 'Заинтересованный': ('#D9EAD3', '#274E13'),
}
NO_BASE = 'нет в базе'
FACTS = ('city', 'material', 'budget', 'name', 'callback_time')
STATUS_ORDER = ('ndz', 'callback', 'not_relevant', 'interested')


def prices() -> tuple[int, int, int]:
    """Первичный звонок, повторный (после НДЗ), «Заинтересованный» — как в «Сводке» таблицы."""
    try:
        first, repeat, interested = (int(p) for p in os.getenv('KITCHEN_PRICES', '10,5,150').split(','))
        return first, repeat, interested
    except ValueError:
        return 10, 5, 150


def price(status: str, number: int) -> int:
    first, repeat, interested = prices()
    if status == 'interested':
        return interested
    return first if number == 0 else repeat


def attempt_label(status: str, number: int) -> str:
    return NDZ_LABELS[min(number, 2)] if status == 'ndz' else LABELS[status]


def loads(value) -> dict:
    return value if isinstance(value, dict) else json.loads(value or '{}')


def stamp(moment, tz, fmt='%d.%m.%Y %H:%M') -> str:
    return moment.astimezone(tz).strftime(fmt) if moment else ''


def leads_of(attempts) -> dict[str, list]:
    """Попытки по номеру телефона в порядке звонков."""
    leads = {}
    for item in sorted(attempts, key=lambda a: (a['call_started_at'] or datetime.min.replace(tzinfo=timezone.utc), a['call_id'])):
        leads.setdefault(digits(item['client_number']) or f"call-{item['call_id']}", []).append(item)
    return leads


def counted(attempts):
    """Платные попытки: первые три на лида, с номером попытки."""
    for items in leads_of(attempts).values():
        for number, item in enumerate(items[:SLOTS]):
            yield number, item


def calls_table(attempts, base: dict, tz) -> sheets.Table:
    """Строка на номер, как в листе базы: попытки, сумма, данные клиента.

    Разделы по дню последнего звонка («Звонки за 13.10.2026»), свежие сверху, чтобы
    сразу было видно, когда звонили. Дата базы из листа базы стоит в своей колонке.
    """
    table = sheets.Table(CALLS_TAB, WIDTHS, frozen=1, filter_from=0, chip_column=6, chip_span=SLOTS, bold_column=0, chips=CHIPS)
    table.add(HEADER, 'header')
    groups = collections.defaultdict(list)
    for key, items in leads_of(attempts).items():
        lead = base.get(key, {})
        facts = {}
        for item in items:
            for name in FACTS:
                value = (loads(item['data']).get('facts') or {}).get(name)
                if value:
                    facts[name] = value
        last = items[-1]
        remind = (loads(last['data']).get('facts') or {}).get('callback_time') if last['status'] == 'callback' else ''
        notes = [f"{stamp(a['call_started_at'], tz, '%d.%m')} {a['comment'] or ''}".strip() for a in items]
        if len(items) > SLOTS:
            notes.append(f'Всего попыток: {len(items)}, в сумму идут первые {SLOTS}')
        slots = items[:SLOTS]
        row = ['+7' + key if len(key) == 10 else items[0]['client_number'] or '', lead.get('id', ''), lead.get('project', ''),
               lead.get('base') or NO_BASE, remind or '', '\n'.join(notes),
               *[attempt_label(a['status'], i) for i, a in enumerate(slots)], *[''] * (SLOTS - len(slots)),
               sum(price(a['status'], i) for i, a in enumerate(slots)),
               *[stamp(a['call_started_at'], tz) for a in slots], *[''] * (SLOTS - len(slots)),
               *[facts.get(name, '') for name in FACTS]]
        day = stamp(last['call_started_at'], tz, '%d.%m.%Y') or 'без даты'
        groups[day].append((last['call_started_at'], bool(lead.get('base')), row))

    def day_of(label):
        try:
            return datetime.strptime(label, '%d.%m.%Y').date()
        except ValueError:
            return date.min

    for label in sorted(groups, key=day_of, reverse=True):
        rows = groups[label]
        missing = sum(not found for _, found, _ in rows)
        table.add([f'Звонки за {label} · лидов {len(rows)}' + (f' · нет в листе «{LEADS_TAB}»: {missing}' if missing else '')],
                  'section')
        for _, _, row in sorted(rows, key=lambda r: r[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
            table.add(row)
    return table


def days_table(attempts, tz) -> sheets.Table:
    table = sheets.Table(DAYS_TAB, [110, 105, 105, 100, 125, 90], frozen=1)
    table.add(['Дата', 'Первичных звонков', 'Повторных звонков', 'Всего звонков', 'Заинтересованных', 'Сумма, ₽'], 'header')
    by_day = collections.defaultdict(lambda: [0, 0, 0, 0])
    for number, item in counted(attempts):
        day = by_day[item['call_started_at'].astimezone(tz).date() if item['call_started_at'] else date.min]
        day[0 if number == 0 else 1] += 1
        day[2] += item['status'] == 'interested'
        day[3] += price(item['status'], number)
    for day in sorted(by_day, reverse=True):
        first, repeat, interested, money = by_day[day]
        table.add([day.strftime('%d.%m.%Y') if day != date.min else 'без даты', first, repeat, first + repeat, interested, money])
    return table


def summary_table(attempts, tz, now: datetime) -> sheets.Table:
    table = sheets.Table(SUMMARY_TAB, [260, 110, 110, 125, 90])
    first, repeat, interested = prices()
    table.add(['Тарифы'], 'title')
    table.add(['Вид звонка', 'Цена, ₽'], 'header')
    table.add(['Первичный', first])
    table.add(['Повторный (после НДЗ)', repeat])
    table.add(['«Заинтересованный»', interested])
    table.add([])
    table.add([f'Итог: {MANAGER}'], 'title')
    table.add(['Период', 'Первичных звонков', 'Повторных звонков', 'Заинтересованных', 'Сумма, ₽'], 'header')
    today = now.astimezone(tz).date()
    periods = (('Сегодня', today), ('С начала недели', today - timedelta(days=today.weekday())),
               ('С начала месяца', today.replace(day=1)), ('Всего', date.min))
    paid = [(n, a, a['call_started_at'].astimezone(tz).date() if a['call_started_at'] else date.min) for n, a in counted(attempts)]
    for label, start in periods:
        part = [(n, a) for n, a, day in paid if day >= start]
        table.add([label, sum(n == 0 for n, _ in part), sum(n > 0 for n, _ in part),
                   sum(a['status'] == 'interested' for _, a in part), sum(price(a['status'], n) for n, a in part)],
                  'total' if label == 'Всего' else None)
    table.add([])
    table.add(['Итоги попыток'], 'title')
    table.add(['Итог', 'Попыток'], 'header')
    by_status = collections.Counter(a['status'] for a in attempts)
    for status in STATUS_ORDER:
        table.add(['НДЗ' if status == 'ndz' else LABELS[status], by_status[status]])
    table.add(['Лидов (номеров)', len(leads_of(attempts))], 'total')
    table.add([])
    table.add([f"Обновлено ботом {now.astimezone(tz).strftime('%d.%m.%Y %H:%M')} по звонкам из телефонии. "
               f'Сумма: первые {SLOTS} попытки на лида, «Заинтересованный» по своему тарифу вместо обычного. '
               'Ручные правки на листах бота не сохраняются.'], 'note')
    return table


def own_only(requests: list[dict], sheet_ids) -> list[dict]:
    """Последняя защита листов, которые ведут люди: любой запрос не к своему листу — ошибка."""
    allowed = {sheet_ids} if isinstance(sheet_ids, int) else set(sheet_ids)

    def ids(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'sheetId':
                    yield item
                else:
                    yield from ids(item)
        elif isinstance(value, list):
            for item in value:
                yield from ids(item)
    foreign = {i for i in ids(requests) if i not in allowed}
    if foreign:
        raise RuntimeError(f'запрос к чужому листу {sorted(foreign)} отклонён')
    return requests


def own_title(title: str) -> str:
    if title not in (CALLS_TAB, SUMMARY_TAB, DAYS_TAB) or title == LEADS_TAB:
        raise RuntimeError(f'лист «{title}» не принадлежит боту')
    return title


async def write(tables: list[sheets.Table], *, http=None, access_token=None) -> None:
    spreadsheet = os.environ['KITCHEN_SHEET_ID']
    access_token = access_token or await asyncio.to_thread(sheets.token)
    headers = {'Authorization': 'Bearer ' + access_token}
    async with (http or httpx.AsyncClient(timeout=60)) as client:
        async def batch(requests, sheet_ids):
            response = await client.post(sheets.API + spreadsheet + ':batchUpdate', headers=headers,
                                         json={'requests': own_only(requests, sheet_ids)})
            response.raise_for_status()
            return response.json()

        meta = await client.get(sheets.API + spreadsheet, headers=headers,
                                params={'fields': 'sheets(properties(sheetId,title,gridProperties),conditionalFormats)'})
        meta.raise_for_status()
        existing = {s['properties']['title']: s for s in meta.json().get('sheets', [])}
        for table in tables:
            title = own_title(table.title)
            rows = max(len(table.rows) + 50, 200)
            own = existing.get(title)
            if own is None:  # новый лист в конце таблицы, порядок листов людей не меняется
                reply = await batch([{'addSheet': {'properties': {
                    'title': title, 'gridProperties': {'rowCount': rows, 'columnCount': table.width}}}}], [])
                sheet_id = reply['replies'][0]['addSheet']['properties']['sheetId']
                grid_rows, grid_cols, rules = rows, table.width, 0
            else:
                sheet_id = own['properties']['sheetId']
                grid_rows = own['properties']['gridProperties']['rowCount']
                grid_cols = own['properties']['gridProperties']['columnCount']
                rules = len(own.get('conditionalFormats', []))
                grow = []
                if grid_rows < len(table.rows) + 1:
                    grow.append({'appendDimension': {'sheetId': sheet_id, 'dimension': 'ROWS', 'length': rows - grid_rows}})
                    grid_rows = rows
                if grid_cols < table.width:
                    grow.append({'appendDimension': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'length': table.width - grid_cols}})
                    grid_cols = table.width
                if grow:
                    await batch(grow, sheet_id)
            response = await client.post(sheets.API + spreadsheet + f"/values/'{title}':clear", headers=headers, json={})
            response.raise_for_status()
            response = await client.put(sheets.API + spreadsheet + f"/values/'{title}'!A1", headers=headers,
                                        params={'valueInputOption': 'RAW'}, json={'values': table.rows})
            response.raise_for_status()
            await batch(sheets.layout(table, sheet_id, grid_rows, grid_cols, rules), sheet_id)


BASE_HEADING = re.compile(r'База\s+от\s+(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?', re.I)


def base_label(cell: str, year: int) -> str | None:
    """«База от 8.10» и «База от 08.10.2026 (120 номеров)» дают одинаковую метку 08.10.2026."""
    found = BASE_HEADING.match(cell)
    if not found:
        return None
    day, month, given = found.groups()
    full = int(given) + 2000 if given and len(given) == 2 else int(given or year)
    return f'{int(day):02d}.{int(month):02d}.{full}'


def column(header: list[str], name: str, default: int) -> int:
    names = [h.strip().lower() for h in header]
    return names.index(name) if name in names else default


async def base(*, http=None, access_token=None, today: date | None = None) -> dict:
    """id, проект и дата базы по телефону из листа базы. Только чтение.

    Колонки ищутся по шапке («id», «Проект», «Телефон»), числа читаются без
    формата листа: телефон вида 7,98E+10 или с пробелами всё равно находится.
    """
    spreadsheet = os.environ['KITCHEN_SHEET_ID']
    access_token = access_token or await asyncio.to_thread(sheets.token)
    async with (http or httpx.AsyncClient(timeout=60)) as client:
        response = await client.get(sheets.API + spreadsheet + f"/values/'{LEADS_TAB}'!A1:Z",
                                    headers={'Authorization': 'Bearer ' + access_token},
                                    params={'valueRenderOption': 'UNFORMATTED_VALUE', 'dateTimeRenderOption': 'FORMATTED_STRING'})
        if response.status_code == 400:  # листа нет или переименован: данные базы просто не подставляются
            log.warning('лист базы «%s» не прочитан: %s', LEADS_TAB, response.text[:200])
            return {}
        response.raise_for_status()
    rows = response.json().get('values', [])
    header = [str(c) for c in rows[0]] if rows else []
    at_id, at_project, at_phone = column(header, 'id', 0), column(header, 'проект', 1), column(header, 'телефон', 3)
    year = (today or date.today()).year
    found, current = {}, ''
    for row in rows[1:]:
        first = str(row[0]).strip() if row else ''
        label = base_label(first, year)
        if label:
            current = label
            continue
        phone = digits(row[at_phone]) if len(row) > at_phone else ''
        if len(phone) == 10:
            cell = row[at_id] if len(row) > at_id else ''
            found.setdefault(phone, {'id': str(int(cell) if isinstance(cell, float) and cell.is_integer() else cell).strip(),
                                     'project': str(row[at_project]).strip() if len(row) > at_project else '',
                                     'base': current})
    return found


_last_sync = 0.0


async def sync(pool, *, http_factory=httpx.AsyncClient, access_token=None, now: datetime | None = None) -> int:
    attempts = await pool.fetch('SELECT * FROM lead_attempts ORDER BY call_started_at NULLS FIRST, call_id')
    if not attempts:
        return 0
    access_token = access_token or await asyncio.to_thread(sheets.token)
    zone = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', attempts[0]['client_id']) or 'Europe/Moscow')
    leads = await base(http=http_factory(timeout=60), access_token=access_token)
    tables = [calls_table(attempts, leads, zone), summary_table(attempts, zone, now or datetime.now(timezone.utc)),
              days_table(attempts, zone)]
    await write(tables, http=http_factory(timeout=60), access_token=access_token)
    await pool.execute('UPDATE lead_attempts SET written_at=now() WHERE written_at IS NULL')
    return len(attempts)


async def tick(pool) -> None:
    global _last_sync
    await record_missed(pool)
    if not sheet_enabled():
        return
    pending = await pool.fetchval('SELECT count(*) FROM lead_attempts WHERE written_at IS NULL')
    if pending or time.monotonic() - _last_sync > SYNC_MINUTES * 60:
        _last_sync = time.monotonic()
        count = await sync(pool)
        if pending:
            log.info('Прозвон лидов: листы «%s» обновлены, попыток %s, новых %s', MANAGER, count, pending)
