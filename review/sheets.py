"""Зеркало в Google-таблицу: лист «Звонки» и лист «Перезвоны».

База остаётся источником правды: таблица целиком перезаписывается из неё раз
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

from reports import fmt_phone
from .callbacks import KIND_LABELS, STATUS_LABELS
from .render import CATEGORY_LABELS, OUTCOME_LABELS

log = logging.getLogger(__name__)

API = 'https://sheets.googleapis.com/v4/spreadsheets/'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
CALLS_SHEET, TASKS_SHEET = 'Звонки', 'Перезвоны'
HISTORY_DAYS = int(os.getenv('SHEETS_HISTORY_DAYS', '60'))
SYNC_MINUTES = int(os.getenv('SHEETS_SYNC_MINUTES', '10'))
KIND_NAMES = {'cold_first': 'холодный', 'repeat': 'повторный', 'gatekeeper_only': 'секретарь'}
CHANCE_NAMES = {'hot': 'горячий', 'normal': 'был', 'none': 'не было'}

CALLS_HEADER = ['Дата', 'Менеджер', 'Номер', 'Компания', 'Собеседник', 'ЛПР', 'Тип звонка', 'Результат', 'Шанс',
                'Где потерян', 'Ключевой момент', 'Как можно было', 'Потребность', 'Возражение', 'Следующий шаг', 'ID звонка']
TASKS_HEADER = ['Дата', 'Время', 'Примерно', 'Что', 'Менеджер', 'Компания', 'Номер', 'Договорённость', 'Статус', 'ID звонка']


def enabled() -> bool:
    return bool(os.getenv('GOOGLE_SHEET_ID') and os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE'))


def call_row(record, tz) -> list:
    data = record['data']
    data = json.loads(data) if isinstance(data, str) else data
    client = data.get('client') or {}
    moment = data.get('key_moment') or {}
    started = record['call_started_at']
    dmp = client.get('dmp_name') or ('да' if client.get('is_dmp') else '')
    if client.get('dmp_contact'):
        dmp = f"{dmp} {client['dmp_contact']}".strip()
    contact = ', '.join(filter(None, [client.get('contact_name'), client.get('contact_role')]))
    return [started.astimezone(tz).strftime('%d.%m.%Y %H:%M') if started else '', record['manager'] or record['extension'] or '',
            fmt_phone(record['client_number']) or '', client.get('company') or '', contact, dmp,
            KIND_NAMES.get(data.get('kind'), ''), ', '.join(OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or []) or 'нет',
            CHANCE_NAMES.get(data.get('chance'), ''), CATEGORY_LABELS.get(moment.get('category'), ''),
            moment.get('what_happened') or '', moment.get('say_instead') or '', client.get('need') or '',
            client.get('objection') or '', ((client.get('next_step') or {}).get('raw') or ''), record['call_id']]


def task_row(record) -> list:
    return [record['due_date'].strftime('%d.%m.%Y'), record['due_time'].strftime('%H:%M') if record['due_time'] else '',
            'да' if record['date_is_estimate'] else '', KIND_LABELS.get(record['kind'], record['kind']),
            record['manager'] or record['extension'] or '', record['company'] or '', fmt_phone(record['client_number']) or '',
            record['raw'] or '', STATUS_LABELS.get(record['status'], record['status']), record['call_id']]


async def collect(pool) -> tuple[list[list], list[list]]:
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    calls = await pool.fetch(
        """SELECT r.call_id, r.extension, r.data, c.client_number, c.call_started_at, cl.timezone,
                  (SELECT full_name FROM employees e WHERE e.client_id=r.client_id AND e.extension=r.extension AND e.role='manager' LIMIT 1) AS manager
           FROM call_reviews r JOIN calls c ON c.id=r.call_id JOIN clients cl ON cl.id=r.client_id
           WHERE r.created_at >= $1 AND (r.data->>'reviewed')::boolean IS TRUE
           ORDER BY coalesce(c.call_started_at, r.created_at) DESC""", since)
    tasks = await pool.fetch(
        """SELECT t.*, (SELECT full_name FROM employees e WHERE e.client_id=t.client_id AND e.extension=t.extension AND e.role='manager' LIMIT 1) AS manager
           FROM callback_tasks t WHERE t.status='open' OR t.updated_at >= $1
           ORDER BY (t.status <> 'open'), t.due_date, t.due_time NULLS LAST""", since)
    return ([CALLS_HEADER] + [call_row(r, ZoneInfo(r['timezone'])) for r in calls],
            [TASKS_HEADER] + [task_row(r) for r in tasks])


def token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token


async def write(sheets: dict[str, list[list]], *, http=None, access_token=None) -> None:
    sheet_id = os.environ['GOOGLE_SHEET_ID']
    access_token = access_token or await asyncio.to_thread(token)
    headers = {'Authorization': 'Bearer ' + access_token}
    async with (http or httpx.AsyncClient(timeout=60)) as client:
        meta = await client.get(API + sheet_id, params={'fields': 'sheets.properties.title'}, headers=headers)
        meta.raise_for_status()
        existing = {s['properties']['title'] for s in meta.json().get('sheets', [])}
        missing = [title for title in sheets if title not in existing]
        if missing:
            response = await client.post(API + sheet_id + ':batchUpdate', headers=headers, json={
                'requests': [{'addSheet': {'properties': {'title': title}}} for title in missing]})
            response.raise_for_status()
        for title, rows in sheets.items():
            response = await client.post(API + sheet_id + f'/values/{title}:clear', headers=headers, json={})
            response.raise_for_status()
            response = await client.put(API + sheet_id + f"/values/{title}!A1", headers=headers,
                                        params={'valueInputOption': 'RAW'}, json={'values': rows})
            response.raise_for_status()


async def sync(pool) -> None:
    calls, tasks = await collect(pool)
    await write({CALLS_SHEET: calls, TASKS_SHEET: tasks})
    log.info('Google-таблица обновлена: звонков %s, перезвонов %s', len(calls) - 1, len(tasks) - 1)
