"""Reconcile owned contact rows into explicitly configured Google Sheets."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from . import callcenter_quality

import httpx

from . import callcenter_base

HEADERS = ['ID карточки', 'Проект', 'Телефон', 'Категория', 'Итог', 'Клиент', 'Последний звонок',
           'История звонков', 'Ссылки CRM', 'Источники', 'Обновлено', 'Версия']
CATEGORIES = ('qualified', 'reusable', 'competitor')
LABELS = {'qualified': 'Квалифицированный лид', 'reusable': 'Неквалифицированный лид', 'competitor': 'Конкурент клиента'}
API = 'https://sheets.googleapis.com/v4/spreadsheets/'


def _token():
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import Request
    credentials = Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'],
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    credentials.refresh(Request())
    return credentials.token


def destinations():
    value = json.loads(os.getenv('CALLCENTER_EXPORT_SHEETS', '{}'))
    if not isinstance(value, dict) or set(value) != set(CATEGORIES):
        raise ValueError('CALLCENTER_EXPORT_SHEETS должен содержать qualified, reusable и competitor')
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', v) for v in value.values()) or len(set(value.values())) != 3:
        raise ValueError('Некорректные или совпадающие идентификаторы таблиц')
    return value


def card_id(card):
    project, phone = str(card.get('project') or '').strip(), str(card.get('phone') or '').strip()
    if not project or not re.fullmatch(r'\d{10}', phone):
        raise ValueError('Карточка без проекта или корректного номера')
    return 'cc1:' + hashlib.sha256((project + '\0' + phone).encode()).hexdigest()


def text(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value or '')


def _date(value):
    if not value:
        return ''
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        dt = dt.replace(tzinfo=ZoneInfo('Europe/Moscow')) if dt.tzinfo is None else dt.astimezone(ZoneInfo('Europe/Moscow'))
        return dt.strftime('%d.%m.%Y %H:%M')
    except ValueError:
        return str(value)[:30]


def _history(history):
    lines = []
    states = {'complete': 'разговор', 'review': 'требует проверки', 'answering_machine': 'автоответчик',
              'other_topic': 'другая тема', 'unknown': 'исход не установлен'}
    for item in history[-20:]:
        check = item.get('check') or {}
        result = next((d.get('reason') for d in check.get('decisions', []) if d.get('reason')), '')
        state = result or ('живой разговор' if check.get('live') else states.get(check.get('state'), 'исход не установлен'))
        manager = {'olga': 'Ольга', 'oksana': 'Оксана'}.get(item.get('operator'), 'Сотрудник')
        lines.append(f"{_date(item.get('at'))} · {manager} · {str(state)[:250]}")
    if len(history) > 20:
        lines.insert(0, f'Показаны последние 20 из {len(history)} звонков; полная история в CRM')
    return '\n'.join(lines)[:12000]


def _sources(sources):
    lines = []
    for source in sources:
        line = ' · '.join(str(source.get(k) or '') for k in ('project', 'tab', 'base', 'tag')).strip(' ·')
        if line and line not in lines:
            lines.append(line)
    return '\n'.join(lines)[:5000]


def render(card):
    category = card['category']
    history = card.get('history') or []
    latest = max((str(h.get('at') or '') for h in history), default='')
    project = card.get('project')
    label = callcenter_quality.SCRIPTS.get(project, (project,))[0]
    base_url = os.getenv('CALLCENTER_CRM_URL', '').rstrip('/')
    parts = urlsplit(base_url)
    if parts.scheme != 'https' or not parts.netloc:
        raise ValueError('Не задан корректный CALLCENTER_CRM_URL')
    link = base_url + '/' + str(card.get('history_url') or '')
    reason = str(card.get('reason') or '')
    evidence = '\n'.join(f"{p.get('at', '')}: «{p.get('quote', '')}»" for p in card.get('evidence') or [] if isinstance(p, dict))
    result = '\n'.join(x for x in (reason, evidence, card.get('next_step_text') or '') if x)[:5000]
    return [card_id(card), str(label), str(card.get('phone') or ''), LABELS[category], result,
            str(card.get('client') or ''), _date(latest), _history(history), link,
            _sources(card.get('sources') or []), _date(latest), '1']


def padded(row):
    return (list(row) + [''] * len(HEADERS))[:len(HEADERS)]


async def ensure(pool):
    await pool.execute('''CREATE TABLE IF NOT EXISTS callcenter_export_rows (
        client_id INTEGER NOT NULL, card_id TEXT NOT NULL, category TEXT NOT NULL,
        sheet_id TEXT NOT NULL, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(client_id,card_id,category,sheet_id))''')
    await pool.execute('''CREATE TABLE IF NOT EXISTS callcenter_export_status (
        client_id INTEGER PRIMARY KEY, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_success_at TIMESTAMPTZ)''')


async def status(pool, client_id):
    await ensure(pool)
    row = await pool.fetchrow('SELECT status,message,checked_at,last_success_at FROM callcenter_export_status WHERE client_id=$1', client_id)
    return dict(row) if row else {'status': 'waiting', 'message': 'Синхронизация ещё не выполнялась'}


async def _mark(conn, client_id, state, message=''):
    await conn.execute('''INSERT INTO callcenter_export_status(client_id,status,message,checked_at,last_success_at)
       VALUES($1,$2,$3,now(),CASE WHEN $2='ready' THEN now() END)
       ON CONFLICT(client_id) DO UPDATE SET status=$2,message=$3,checked_at=now(),
       last_success_at=CASE WHEN $2='ready' THEN now() ELSE callcenter_export_status.last_success_at END''',
       client_id, state, message[:500])


async def _read(http, sheet):
    response = await http.get(API + sheet + '/values/' + quote("'Лиды'", safe=''), params={'valueRenderOption': 'FORMULA'})
    response.raise_for_status()
    rows = response.json().get('values', [])
    if not rows or padded(rows[0]) != HEADERS:
        raise ValueError('Заголовки вкладки «Лиды» не совпадают с ожидаемыми')
    seen = {}
    for number, row in enumerate(rows[1:], 2):
        if row and str(row[0]).startswith('cc1:'):
            if row[0] in seen:
                raise ValueError('В таблице повторяется ID карточки; требуется проверка')
            seen[row[0]] = number
    return rows, seen


async def _put(http, sheet, row, values):
    url = API + sheet + '/values/' + quote(f"'Лиды'!A{row}:L{row}", safe='')
    response = await http.put(url, params={'valueInputOption': 'RAW'}, json={'values': [values]})
    response.raise_for_status()


async def _grow(http, sheet, required_row):
    response = await http.get(API + sheet, params={'fields': 'sheets(properties(sheetId,title,gridProperties(rowCount)))'})
    response.raise_for_status()
    matches = [x['properties'] for x in response.json().get('sheets', []) if x['properties'].get('title') == 'Лиды']
    if len(matches) != 1:
        raise ValueError('Вкладка «Лиды» не найдена')
    props = matches[0]
    current = props['gridProperties']['rowCount']
    if required_row > current:
        response = await http.post(API + sheet + ':batchUpdate', json={'requests': [{'appendDimension': {
            'sheetId': props['sheetId'], 'dimension': 'ROWS', 'length': max(1000, required_row - current)}}]})
        response.raise_for_status()


async def sync(pool, client_id):
    if not os.getenv('CALLCENTER_EXPORT_SHEETS'):
        return
    await ensure(pool)
    async with pool.acquire() as conn:
        locked = await conn.fetchval('SELECT pg_try_advisory_lock($1,$2)', 1128481876, int(client_id))
        if not locked:
            return
        try:
            await _mark(conn, client_id, 'syncing')
            sheets = destinations()
            from . import callcenter_insights
            cards = await callcenter_insights.cards(pool, client_id)
            # An unavailable source is never interpreted as an empty or complete dataset.
            folder = os.getenv('CALLCENTER_LEADS_FOLDER_ID', '').strip()
            if not folder:
                raise ValueError('Не задана папка исходных таблиц')
            _, books = await callcenter_base.snapshot(folder)
            if any(books.get(op) is None for op in callcenter_base.TITLES):
                raise ValueError('Исходные таблицы временно недоступны')
            wanted = {}
            stopped = {}
            for card in cards:
                if card.get('stop_contact'):
                    stopped[card_id(card)] = card
                    continue
                if card.get('category') not in CATEGORIES:
                    continue
                row = render(card)
                if row[0] in wanted:
                    raise ValueError('Повтор карточки в исходных данных')
                wanted[row[0]] = (card['category'], row)
            access = await asyncio.wait_for(asyncio.to_thread(_token), 10)
            async with httpx.AsyncClient(timeout=15, headers={'Authorization': 'Bearer ' + access}) as http:
                pages = {cat: await _read(http, sheet) for cat, sheet in sheets.items()}
                records = await conn.fetch('SELECT card_id,category,sheet_id,payload FROM callcenter_export_rows WHERE client_id=$1', client_id)
                known = {(r['card_id'], r['category'], r['sheet_id']): (json.loads(r['payload']) if isinstance(r['payload'], str) else r['payload']) for r in records}
                errors = []
                # An interrupted first write may leave a row before its ownership record was saved.
                # Do not silently report a revoked contact as removed when that row cannot be verified.
                for key in stopped:
                    for oldcat, (_, index) in pages.items():
                        if key in index and (key, oldcat, sheets[oldcat]) not in known:
                            errors.append('Не удалось проверить карточку с запретом: ' + key[-8:])
                # A verified opt-out removes only our unchanged row. CRM retains the history.
                for (key, oldcat, oldsheet), previous in list(known.items()):
                    if key not in stopped:
                        continue
                    if oldcat not in pages or sheets.get(oldcat) != oldsheet:
                        errors.append('Изменилась таблица назначения: ' + key[-8:]); continue
                    oldrows, oldindex = pages[oldcat]
                    number = oldindex.get(key)
                    if number:
                        if padded(oldrows[number - 1]) != previous:
                            errors.append('Карточка изменена вручную: ' + key[-8:]); continue
                        await _put(http, oldsheet, number, [''] * len(HEADERS))
                        oldrows[number - 1] = oldrows[number - 1][len(HEADERS):] and ([''] * len(HEADERS) + oldrows[number - 1][len(HEADERS):]) or []
                        del oldindex[key]
                    await conn.execute('DELETE FROM callcenter_export_rows WHERE client_id=$1 AND card_id=$2 AND category=$3 AND sheet_id=$4', client_id, key, oldcat, oldsheet)
                for key, (category, row) in wanted.items():
                    sheet = sheets[category]
                    rows, index = pages[category]
                    number = index.get(key)
                    previous = known.get((key, category, sheet))
                    if number:
                        current = padded(rows[number - 1])
                        if current != row and (previous is None or current != previous):
                            errors.append('Карточка изменена вручную: ' + key[-8:]); continue
                    else:
                        # Append to the physical end, never overwrite unrelated content.
                        number = next((n for n, existing in enumerate(rows[1:], 2) if not any(existing)), len(rows) + 1)
                    if number > len(rows) or padded(rows[number - 1]) != row:
                        await _grow(http, sheet, number)
                        await _put(http, sheet, number, row)
                        if number > len(rows): rows.append(row)
                        else: rows[number - 1] = row
                        index[key] = number
                    await conn.execute('''INSERT INTO callcenter_export_rows(client_id,card_id,category,sheet_id,payload)
                        VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT(client_id,card_id,category,sheet_id)
                        DO UPDATE SET payload=excluded.payload,updated_at=now()''', client_id, key, category, sheet, json.dumps(row, ensure_ascii=False))
                    # Remove only a previously written, unchanged row after the destination is durable.
                    for (oldkey, oldcat, oldsheet), old in list(known.items()):
                        if oldkey != key or (oldcat == category and oldsheet == sheet): continue
                        if oldcat not in pages or sheets.get(oldcat) != oldsheet:
                            errors.append('Изменилась таблица назначения: ' + key[-8:]); continue
                        oldrows, oldindex = pages[oldcat]
                        oldnum = oldindex.get(key)
                        if oldnum and padded(oldrows[oldnum - 1]) != old:
                            errors.append('Старая карточка изменена вручную: ' + key[-8:]); continue
                        if oldnum:
                            await _put(http, oldsheet, oldnum, [''] * len(HEADERS))
                            oldrows[oldnum - 1] = [''] * len(HEADERS)
                            del oldindex[key]
                        await conn.execute('DELETE FROM callcenter_export_rows WHERE client_id=$1 AND card_id=$2 AND category=$3 AND sheet_id=$4', client_id, key, oldcat, oldsheet)
                if errors:
                    raise ValueError('; '.join(errors[:5]))
            await _mark(conn, client_id, 'ready')
        except Exception as exc:
            await _mark(conn, client_id, 'error', f'{type(exc).__name__}: {exc}')
        finally:
            await conn.execute('SELECT pg_advisory_unlock($1,$2)', 1128481876, int(client_id))
