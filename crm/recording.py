"""Запись звонка из Mango для прослушивания в CRM.

Записи хранит Mango; бот знает recording_id каждого звонка (calls.recording_id).
Ссылка Mango на файл одноразовая, поэтому файл качает сервер и отдаёт странице
сам, с поддержкой Range: плеер браузера просит файл кусками при перемотке.
Последние записи держим в памяти, чтобы перемотка не качала файл заново.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict

from tools import Forbidden, is_privileged

import mango_client
from crypto_util import decrypt

log = logging.getLogger(__name__)

CACHE_BYTES = 80 * 1024 * 1024
_cache: OrderedDict[int, tuple[bytes, str]] = OrderedDict()
_loading: dict[int, asyncio.Future] = {}


class NoRecording(LookupError):
    pass


def kind(data: bytes) -> str:
    if data[:4] == b'RIFF':
        return 'audio/wav'
    if data[:4] == b'OggS':
        return 'audio/ogg'
    return 'audio/mpeg'


def _remember(call_id: int, item: tuple[bytes, str]) -> None:
    _cache[call_id] = item
    _cache.move_to_end(call_id)
    while sum(len(v[0]) for v in _cache.values()) > CACHE_BYTES and len(_cache) > 1:
        _cache.popitem(last=False)


async def _download(pool, client_id: int, recording_id: str) -> tuple[bytes, str]:
    client = await pool.fetchrow('SELECT vpbx_api_key_enc, vpbx_api_salt_enc FROM clients WHERE id=$1', client_id)
    if not client or not client['vpbx_api_key_enc']:
        raise NoRecording('Mango не подключён.')
    try:
        data = await mango_client.fetch_recording(decrypt(client['vpbx_api_key_enc']),
                                                  decrypt(client['vpbx_api_salt_enc']), recording_id)
    except Exception as exc:
        log.warning('запись %s не скачалась: %s', recording_id, exc)
        raise NoRecording('Mango не отдал запись. Попробуйте позже.') from exc
    if not data:
        raise NoRecording('Mango отдал пустую запись.')
    return data, kind(data)


async def get(pool, actor, call_id: int) -> tuple[bytes, str]:
    """Файл записи и его тип. Менеджер слушает только свои звонки."""
    row = await pool.fetchrow('SELECT client_id, extension, recording_id FROM calls WHERE id=$1 AND client_id=$2',
                              call_id, actor.client_id)
    if row is None:
        raise NoRecording('Такого звонка нет.')
    if not is_privileged(actor) and row['extension'] != actor.extension:
        raise Forbidden('Это звонок другого менеджера.')
    if not row['recording_id']:
        raise NoRecording('У этого звонка нет записи в Mango.')
    if call_id in _cache:
        _cache.move_to_end(call_id)
        return _cache[call_id]
    # Плеер шлёт несколько запросов сразу: качаем один раз.
    if call_id not in _loading:
        _loading[call_id] = asyncio.ensure_future(_download(pool, row['client_id'], row['recording_id']))
    task = _loading[call_id]
    try:
        item = await asyncio.shield(task)
    finally:
        if task.done():
            _loading.pop(call_id, None)
    _remember(call_id, item)
    return item


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Range: bytes=a-b в (начало, конец включительно); None, если весь файл."""
    m = re.fullmatch(r'bytes=(\d*)-(\d*)', (header or '').strip())
    if not m or (not m[1] and not m[2]) or size == 0:
        return None
    if not m[1]:
        start = max(0, size - int(m[2]))
        return start, size - 1
    start = int(m[1])
    end = min(int(m[2]), size - 1) if m[2] else size - 1
    if start > end:
        raise ValueError('Неверный диапазон.')
    return start, end
