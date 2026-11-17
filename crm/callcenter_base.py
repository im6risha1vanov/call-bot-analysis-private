"""Read operator lead sheets and attribute call results to their project tabs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import httpx

from . import cabinet, callcenter_match


log = logging.getLogger(__name__)
DRIVE = 'https://www.googleapis.com/drive/v3/files'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'
TITLES = {'olga': 'ольга', 'oksana': 'оксана'}
TTL = 300
_cache = None
_lock = asyncio.Lock()


async def _google(http, url, **kwargs):
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


async def _folder_files(http, folder):
    params = {'q': f"'{folder}' in parents and trashed=false and mimeType='application/vnd.google-apps.spreadsheet'",
              'fields': 'nextPageToken,files(id,name)', 'pageSize': 100,
              'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
    while True:
        page = await _google(http, DRIVE, params=params)
        yield page.get('files', [])
        if not page.get('nextPageToken'):
            break
        params['pageToken'] = page['nextPageToken']

async def _workbook(http, sheet_id):
    url = f'{SHEETS}/{sheet_id}'
    metadata = await _google(http, url, params={'fields': 'sheets(properties(title,sheetType,hidden))'})
    tabs = [item['properties']['title'] for item in metadata.get('sheets', [])
            if item['properties'].get('sheetType', 'GRID') == 'GRID' and not item['properties'].get('hidden')]
    if not tabs:
        return {}
    ranges = {name: "'" + name.replace("'", "''") + "'" for name in tabs}

    async def values(names, cells):
        result = await _google(http, url + '/values:batchGet', params={
            'ranges': [f'{ranges[name]}!{cells}' for name in names], 'valueRenderOption': 'FORMATTED_VALUE'})
        pages = result.get('valueRanges', [])
        if len(pages) != len(names):
            raise ValueError('Incomplete Google Sheets response')
        return {name: rows.get('values', []) for name, rows in zip(names, pages)}

    # Производные листы бота и сводки не участвуют: только таблицы с исходной базой.
    headers = await values(tabs, 'A1:P1')
    projects = [name for name, rows in headers.items() if rows and
                {'id', 'Проект', 'Тег проекта', 'Телефон', 'Дата базы'} <= set(rows[0])]
    return {name: cabinet.sheet_records(rows) for name, rows in (await values(projects, 'A1:P')).items()} if projects else {}


async def snapshot(folder):
    global _cache
    async with _lock:
        stamp = time.monotonic()
        if _cache and _cache[0] == folder and stamp - _cache[1] < TTL:
            return _cache[2], _cache[3]
        checked_at = datetime.now(timezone.utc).isoformat()
        books = {operator: None for operator in TITLES}
        try:
            from review.presentations import token
            access = await asyncio.wait_for(asyncio.to_thread(token), 8)
            async with (asyncio.timeout(20),
                        httpx.AsyncClient(timeout=8, headers={'Authorization': 'Bearer ' + access}) as http):
                found = defaultdict(list)
                async for page in _folder_files(http, folder):
                    for item in page:
                        found[item['name'].strip().casefold()].append(item['id'])
                for operator, title in TITLES.items():
                    ids = found[title]
                    if len(ids) != 1:
                        continue
                    try:
                        books[operator] = await _workbook(http, ids[0])
                    except (httpx.HTTPError, ValueError) as exc:
                        log.warning('lead sheet unavailable for %s (%s)', operator, type(exc).__name__)
        except Exception as exc:
            log.warning('lead folder unavailable (%s)', type(exc).__name__)
        _cache = (folder, stamp, checked_at, books)
        return checked_at, books


def _phone_key(value):
    digits = callcenter_match.digits(value)
    if len(digits) == 10:
        return digits
    return digits[1:] if len(digits) == 11 and digits[0] in '78' else ''


def report(book, rows, day, checked_at):
    if not book:
        return {'checked_at': checked_at, 'status': 'unavailable'}
    numbers = defaultdict(set)
    dated = defaultdict(set)
    sizes, valid_rows = {}, 0
    invalid_rows = 0
    for tab, leads in book.items():
        unique = set()
        for lead in leads:
            phone = _phone_key(lead['phone'])
            if not phone:
                invalid_rows += 1
                continue
            numbers[phone].add(tab)
            unique.add(phone)
            valid_rows += 1
            if any(a['result'] and a['date'] == day for a in lead['attempts']):
                dated[phone].add(tab)
        sizes[tab] = len(unique)

    dialed = defaultdict(list)
    for row in rows:
        phone = _phone_key(row['phone'])
        if phone:
            dialed[phone].append(row)
    matches = defaultdict(Counter)
    ambiguous, absent = 0, 0
    for phone, calls in dialed.items():
        candidates = dated.get(phone) or numbers.get(phone, set())
        if len(candidates) != 1:
            ambiguous += len(candidates) > 1
            absent += not candidates
            continue
        counters = matches[next(iter(candidates))]
        counters['matched'] += 1
        live = any((row['script_check'] or {}).get('live') for row in calls)
        counters['live'] += live
        counters['no_contact'] += all(not row['duration_s'] or (row['script_check'] or {}).get('state') == 'answering_machine'
                                      for row in calls) and not live
    return {'checked_at': checked_at, 'status': 'ready', 'sheet_contacts': len(numbers),
            'duplicate_rows': valid_rows - len(numbers), 'invalid_rows': invalid_rows, 'ambiguous': ambiguous,
            'absent': absent, 'matched': sum(m['matched'] for m in matches.values()), 'projects': [
                {'title': tab, 'contacts': size, 'matched': matches[tab]['matched'],
                 'live': matches[tab]['live'], 'no_contact': matches[tab]['no_contact']}
                for tab, size in sizes.items()]}


async def refresh_latest(pool, client_id, prefix):
    folder = os.getenv('CALLCENTER_LEADS_FOLDER_ID', '').strip()
    if not folder or not all(c.isascii() and (c.isalnum() or c in '-_') for c in folder):
        return
    checked_at, books = await snapshot(folder)
    latest = await pool.fetch(
        """SELECT * FROM (
             SELECT DISTINCT ON (operator) id, operator, period_start, result, finished_at
             FROM call_analysis_runs WHERE client_id=$1 AND dedup_key LIKE $2 AND status='done' AND result ? 'quality'
             ORDER BY operator,period_start DESC,id DESC
           ) AS latest WHERE result #>> '{quality,sheet_base,checked_at}' IS DISTINCT FROM $3""",
        client_id, prefix + '%', checked_at)
    for run in latest:
        rows = await pool.fetch('SELECT phone,duration_s,stages FROM call_analysis_items WHERE run_id=$1', run['id'])
        calls = [dict(row, script_check=(json.loads(row['stages']) if isinstance(row['stages'], str)
                                       else row['stages']).get('script_check')) for row in rows]
        result = report(books.get(run['operator']), calls, run['period_start'].isoformat(), checked_at)
        await pool.execute("""UPDATE call_analysis_runs SET result=jsonb_set(result,'{quality,sheet_base}',$2::jsonb)
                             WHERE id=$1 AND status='done' AND finished_at=$3""",
                           run['id'], json.dumps(result, ensure_ascii=False), run['finished_at'])
