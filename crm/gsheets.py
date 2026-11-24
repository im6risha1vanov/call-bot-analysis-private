"""Чтение Google Таблиц для CRM: таблица бота и «Прозвон лидов».

Ключ сервисного аккаунта тот же, что у бота. Читаем только две разрешённые
таблицы и только значения. Ответы держим в памяти две минуты, чтобы несколько
открытых страниц не упирались в лимиты Google.
"""
from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

import httpx

CACHE_SEC = 120
TOKEN_SEC = 45 * 60
_client = httpx.AsyncClient(timeout=30.0)
_cache: dict[tuple[str, str], tuple[float, list]] = {}
_token: tuple[float, str] | None = None
_lock = asyncio.Lock()


def allowed() -> set[str]:
    return {v for v in (os.getenv('GOOGLE_SHEET_ID'), os.getenv('KITCHEN_SHEET_ID')) if v}


def leads_sheet() -> str | None:
    return os.getenv('KITCHEN_SHEET_ID') or None


async def _bearer() -> str:
    global _token
    async with _lock:
        if _token is None or time.monotonic() - _token[0] > TOKEN_SEC:
            from review.sheets import token
            _token = (time.monotonic(), await asyncio.to_thread(token))
        return _token[1]


async def values(spreadsheet_id: str, range_: str) -> list[list[str]]:
    if spreadsheet_id not in allowed():
        raise PermissionError('Эта таблица CRM недоступна.')
    key = (spreadsheet_id, range_)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SEC:
        return hit[1]
    url = f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(range_, safe="")}'
    r = await _client.get(url, headers={'Authorization': 'Bearer ' + await _bearer()})
    if r.status_code == 400 and 'Unable to parse range' in r.text:
        raise LookupError('Такого листа нет.')
    r.raise_for_status()
    rows = r.json().get('values', [])
    _cache[key] = (time.monotonic(), rows)
    return rows
