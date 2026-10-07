"""Прозвон лидов по скрипту «Кухонные гарнитуры»: итог попытки в таблицу владельца.

У оператора (KITCHEN_EXTENSIONS) свой скрипт и своя таблица «Прозвон лидов»,
которую раньше заполняли руками. После каждого исходящего звонка бот ставит
итог следующей пустой попытки («Звонок 1/2/3») в строке лида с этим телефоном
и дописывает строку в «Комментарий». Остальные колонки (даты, сумму, сводки)
считают формулы самой таблицы; бот их не трогает и строк не добавляет.

Недозвон (звонок без разговора) записывается как НДЗ без запроса к модели;
разговор разбирается промтом review/kitchen_prompt.md. «Заинтересованный»
оплачивается дороже остальных, поэтому код ставит его только при полном
чек-листе из памятки оператора, иначе понижает до «Перезвон» с пометкой,
чего не хватает.

Настройки: KITCHEN_EXTENSIONS (добавочные через запятую), KITCHEN_SHEET_ID,
KITCHEN_SHEET_TAB (лист с лидами), KITCHEN_REGIONS (регионы доставки через
запятую; пусто — регион не проверяется), KITCHEN_SINCE (ISO-время, с которого
недозвоны пишутся в таблицу, чтобы не задним числом).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
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
PHONE_COL, COMMENT_COL, ATTEMPT_COLS = 3, 7, (8, 9, 10)  # D, H, I/J/K
LETTERS = 'ABCDEFGHIJK'
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


def plan(grid: list[list], attempts, today: str) -> tuple[list[tuple[str, str]], dict]:
    """Какие ячейки записать. grid — значения листа с A1, attempts — по порядку звонков.

    Возвращает (обновления [(A1-ячейка, значение)], {call_id: (ячейка или None, ошибка или None)})."""
    grid = [list(row) + [''] * (len(LETTERS) - len(row)) for row in grid]
    updates, results = {}, {}
    for attempt in attempts:
        phone = digits(attempt['client_number'])
        rows = [i for i, row in enumerate(grid) if phone and len(phone) == 10 and digits(row[PHONE_COL]) == phone]
        if not rows:
            results[attempt['call_id']] = (None, 'номер не найден в таблице')
            continue
        free = [i for i in rows if any(str(grid[i][c]).strip() == '' for c in ATTEMPT_COLS)]
        index = (free or rows)[-1]  # нижняя строка: самая свежая загрузка базы
        row = grid[index]
        column = next((c for c in ATTEMPT_COLS if str(row[c]).strip() == ''), None)
        cell = None
        if column is not None:
            number = ATTEMPT_COLS.index(column)
            value = NDZ_LABELS[number] if attempt['status'] == 'ndz' else LABELS[attempt['status']]
            cell = f'{LETTERS[column]}{index + 1}'
            row[column] = value
            updates[cell] = value
        note = f"{today} {attempt['comment'] or ''}".strip() + ('' if column is not None else ' (сверх трёх попыток)')
        row[COMMENT_COL] = (str(row[COMMENT_COL]).rstrip() + '\n' if str(row[COMMENT_COL]).strip() else '') + note
        updates[f'{LETTERS[COMMENT_COL]}{index + 1}'] = row[COMMENT_COL]
        results[attempt['call_id']] = (cell or f'{LETTERS[COMMENT_COL]}{index + 1}', None)
    return list(updates.items()), results


async def flush(pool, *, http=None, access_token=None) -> int:
    attempts = await pool.fetch(
        'SELECT * FROM lead_attempts WHERE written_at IS NULL ORDER BY call_started_at NULLS FIRST, call_id LIMIT 200')
    if not attempts:
        return 0
    spreadsheet, tab = os.environ['KITCHEN_SHEET_ID'], os.getenv('KITCHEN_SHEET_TAB', 'Кухонные гарнитуры')
    access_token = access_token or await asyncio.to_thread(sheets.token)
    headers = {'Authorization': 'Bearer ' + access_token}
    zone = await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', attempts[0]['client_id'])
    today = datetime.now(ZoneInfo(zone or 'Europe/Moscow')).strftime('%d.%m')
    async with (http or httpx.AsyncClient(timeout=60)) as client:
        response = await client.get(sheets.API + spreadsheet + f"/values/'{tab}'!A1:K", headers=headers,
                                    params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        response.raise_for_status()
        updates, results = plan(response.json().get('values', []), attempts, today)
        if updates:
            response = await client.post(sheets.API + spreadsheet + '/values:batchUpdate', headers=headers, json={
                'valueInputOption': 'RAW',
                'data': [{'range': f"'{tab}'!{cell}", 'values': [[value]]} for cell, value in updates]})
            response.raise_for_status()
    for call_id, (cell, error) in results.items():
        await pool.execute('UPDATE lead_attempts SET written_at=now(), sheet_cell=$2, error=$3 WHERE call_id=$1',
                           call_id, cell, error)
        if error:
            log.warning('прозвон кухонь: call id=%s — %s', call_id, error)
    return len(results)


async def tick(pool) -> None:
    await record_missed(pool)
    if sheet_enabled():
        written = await flush(pool)
        if written:
            log.info('Прозвон лидов: записано попыток %s', written)
