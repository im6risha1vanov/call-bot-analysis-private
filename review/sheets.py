"""Зеркало в Google-таблицу: один лист «Клиенты», строка на номер клиента.

Колонки как в CRM-выгрузке владельца (образец «Кух.гарнитура ГЦК»): id, Проект,
Тег проекта, Телефон, Создано, Напоминание, Заметка, Комментарий, Статус, Сумма.
В строке последний разобранный звонок с этим номером и открытый перезвон по нему.

База остаётся источником правды: лист целиком перезаписывается из неё раз
в SHEETS_SYNC_MINUTES, правки в самой таблице не читаются (статус перезвона
меняется кнопками в Telegram). Включается двумя переменными:
GOOGLE_SHEET_ID и GOOGLE_SERVICE_ACCOUNT_FILE (JSON ключ сервисного аккаунта,
которому дан доступ «Редактор» к таблице).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from .render import OUTCOME_LABELS

log = logging.getLogger(__name__)

API = 'https://sheets.googleapis.com/v4/spreadsheets/'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEET = 'Клиенты'
OLD_SHEETS = ('Звонки', 'Перезвоны')  # первая версия зеркала, удаляются при синхронизации
HISTORY_DAYS = int(os.getenv('SHEETS_HISTORY_DAYS', '60'))
SYNC_MINUTES = int(os.getenv('SHEETS_SYNC_MINUTES', '10'))

HEADER = ['id', 'Проект', 'Тег проекта', 'Телефон', 'Создано', 'Напоминание', 'Заметка', 'Комментарий', 'Статус', 'Сумма']
WIDTHS = [110, 220, 170, 120, 150, 140, 320, 380, 150, 80]
STATUS_BY_OUTCOME = {'deal': 'Сделка', 'meeting': 'Встреча', 'proposal': 'КП', 'dmp_contact': 'Контакт ЛПР',
                     'callback_date': 'Перезвон'}
STATUS_COLORS = {  # фон ячейки «Статус»
    'Сделка': (0.72, 0.88, 0.72), 'Встреча': (0.72, 0.88, 0.72), 'КП': (0.80, 0.90, 0.80),
    'Контакт ЛПР': (0.80, 0.90, 0.80), 'Перезвон': (1.0, 0.95, 0.70), 'Перезвон выполнен': (0.88, 0.88, 0.88),
    'Потерян, шанс был': (0.98, 0.78, 0.76), 'Отказ': (0.90, 0.90, 0.90),
}


def enabled() -> bool:
    return bool(os.getenv('GOOGLE_SHEET_ID') and os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE'))


def status(data: dict, task) -> str:
    if task is not None and task['status'] == 'open':
        return 'Встреча' if task['kind'] == 'meeting' else 'Перезвон'
    if data.get('success'):
        for outcome in ('deal', 'meeting', 'proposal', 'dmp_contact', 'callback_date'):
            if outcome in (data.get('outcome') or []):
                return 'Перезвон выполнен' if outcome == 'callback_date' and task is not None and task['status'] == 'done' \
                    else STATUS_BY_OUTCOME[outcome]
    if task is not None and task['status'] == 'done':
        return 'Перезвон выполнен'
    return 'Потерян, шанс был' if data.get('chance') in {'hot', 'normal'} else 'Отказ'


def note(data: dict) -> str:
    client = data.get('client') or {}
    contact = ', '.join(filter(None, [client.get('contact_name'), client.get('contact_role')]))
    dmp = client.get('dmp_name') or ''
    parts = [f'Собеседник: {contact}' if contact else '',
             f"ЛПР: {dmp} {client.get('dmp_contact') or ''}".strip() if dmp or client.get('dmp_contact') else '',
             f"Нужно: {client['need']}" if client.get('need') else '',
             f"Сейчас: {client['current_solution']}" if client.get('current_solution') else '',
             f"Возражение: {client['objection']}" if client.get('objection') else '']
    return '\n'.join(p for p in parts if p)


def comment(data: dict) -> str:
    step = ((data.get('client') or {}).get('next_step') or {}).get('raw')
    if data.get('success'):
        result = ', '.join(OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or [])
        return '\n'.join(filter(None, [f'Результат: {result}' if result else '', f'Договорились: {step}' if step else '']))
    moment = data.get('key_moment') or {}
    parts = [moment.get('what_happened') or '',
             f"Как можно было: «{moment['say_instead']}»" if moment.get('say_instead') else '',
             f'Договорились: {step}' if step else '']
    return '\n'.join(p for p in parts if p)


def row(record, task, tz) -> list:
    data = record['data']
    data = json.loads(data) if isinstance(data, str) else data
    started = record['call_started_at']
    reminder = ''
    if task is not None and task['status'] == 'open':
        reminder = task['due_date'].strftime('%Y-%m-%d') + (' ' + task['due_time'].strftime('%H:%M') if task['due_time'] else '')
        if task['date_is_estimate']:
            reminder += ' (примерно)'
    return [record['call_id'], (data.get('client') or {}).get('company') or 'Компания не названа',
            record['manager'] or record['extension'] or '', record['client_number'] or '',
            started.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S') if started else '', reminder,
            note(data), comment(data), status(data, task), '']


async def collect(pool) -> list[list]:
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    calls = await pool.fetch(
        """SELECT DISTINCT ON (r.client_id, coalesce(c.client_number, r.call_id::text))
                  r.call_id, r.client_id, r.extension, r.data, c.client_number, c.call_started_at, cl.timezone,
                  (SELECT full_name FROM employees e WHERE e.client_id=r.client_id AND e.extension=r.extension AND e.role='manager' LIMIT 1) AS manager
           FROM call_reviews r JOIN calls c ON c.id=r.call_id JOIN clients cl ON cl.id=r.client_id
           WHERE r.created_at >= $1 AND (r.data->>'reviewed')::boolean IS TRUE
           ORDER BY r.client_id, coalesce(c.client_number, r.call_id::text), coalesce(c.call_started_at, r.created_at) DESC""", since)
    tasks = {(t['client_id'], t['client_number']): t for t in await pool.fetch(
        """SELECT DISTINCT ON (client_id, client_number) * FROM callback_tasks
           WHERE client_number IS NOT NULL ORDER BY client_id, client_number, (status <> 'open'), updated_at DESC""")}
    rows = []
    for r in calls:
        task = tasks.get((r['client_id'], r['client_number']))
        if task is not None and task['status'] != 'open' and task['call_id'] != r['call_id']:
            task = None  # закрытая задача от более раннего звонка к последнему звонку не относится
        rows.append((r['call_started_at'] or datetime.min.replace(tzinfo=timezone.utc), row(r, task, ZoneInfo(r['timezone']))))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [HEADER] + [r for _, r in rows]


def token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token


def layout(sheet_id: int, rows: int) -> list[dict]:
    """Шапка жирная и закреплена, фильтр, ширины колонок, перенос текста, цвет статуса."""
    end = max(rows, 2)
    requests = [
        {'updateSheetProperties': {'properties': {'sheetId': sheet_id, 'gridProperties': {'frozenRowCount': 1}},
                                   'fields': 'gridProperties.frozenRowCount'}},
        {'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {'userEnteredFormat': {'textFormat': {'bold': True},
                                                       'backgroundColor': {'red': 0.93, 'green': 0.93, 'blue': 0.93}}},
                        'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
        {'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': end},
                        'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP', 'verticalAlignment': 'TOP'}},
                        'fields': 'userEnteredFormat(wrapStrategy,verticalAlignment)'}},
        {'setBasicFilter': {'filter': {'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': end,
                                                 'startColumnIndex': 0, 'endColumnIndex': len(HEADER)}}}},
    ]
    for i, width in enumerate(WIDTHS):
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': i, 'endIndex': i + 1},
            'properties': {'pixelSize': width}, 'fields': 'pixelSize'}})
    column = HEADER.index('Статус')
    for label, (red, green, blue) in STATUS_COLORS.items():
        requests.append({'addConditionalFormatRule': {'index': 0, 'rule': {
            'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': end,
                        'startColumnIndex': column, 'endColumnIndex': column + 1}],
            'booleanRule': {'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': label}]},
                            'format': {'backgroundColor': {'red': red, 'green': green, 'blue': blue}}}}}})
    return requests


async def write(rows: list[list], *, http=None, access_token=None) -> None:
    spreadsheet = os.environ['GOOGLE_SHEET_ID']
    access_token = access_token or await asyncio.to_thread(token)
    headers = {'Authorization': 'Bearer ' + access_token}
    async with (http or httpx.AsyncClient(timeout=60)) as client:
        async def batch(requests):
            response = await client.post(API + spreadsheet + ':batchUpdate', headers=headers, json={'requests': requests})
            response.raise_for_status()
            return response.json()

        meta = await client.get(API + spreadsheet, headers=headers,
                                params={'fields': 'sheets(properties(sheetId,title),conditionalFormats)'})
        meta.raise_for_status()
        sheets = {s['properties']['title']: s for s in meta.json().get('sheets', [])}
        if SHEET not in sheets:
            reply = await batch([{'addSheet': {'properties': {'title': SHEET, 'index': 0}}}])
            sheet_id, rules = reply['replies'][0]['addSheet']['properties']['sheetId'], 0
        else:
            sheet_id, rules = sheets[SHEET]['properties']['sheetId'], len(sheets[SHEET].get('conditionalFormats', []))
        old = [{'deleteSheet': {'sheetId': sheets[t]['properties']['sheetId']}} for t in OLD_SHEETS if t in sheets]
        if old:
            await batch(old)
        response = await client.post(API + spreadsheet + f'/values/{SHEET}:clear', headers=headers, json={})
        response.raise_for_status()
        response = await client.put(API + spreadsheet + f'/values/{SHEET}!A1', headers=headers,
                                    params={'valueInputOption': 'RAW'}, json={'values': rows})
        response.raise_for_status()
        # Правила цвета пересоздаются целиком, чтобы не копились при каждой синхронизации.
        await batch([{'deleteConditionalFormatRule': {'sheetId': sheet_id, 'index': 0}} for _ in range(rules)]
                    + layout(sheet_id, len(rows)))


async def sync(pool) -> None:
    rows = await collect(pool)
    await write(rows)
    log.info('Google-таблица обновлена: клиентов %s', len(rows) - 1)
