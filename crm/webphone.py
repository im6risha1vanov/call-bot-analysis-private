"""Телефон в браузере: менеджер звонит прямо из вкладки CRM, без Talker.

Браузер (static/webphone.js, библиотека JsSIP) говорит SIP по WebSocket. Сокет
идёт через этот сайт (/webphone/ws) и SSH-туннель робота в Asterisk на
российском VPS, а Asterisk звонит клиенту через SIP-учётку добавочного
менеджера в Mango. Для Mango это обычный звонок менеджера: запись, «Звонки»
и разбор как раньше. Звук идёт напрямую браузер ↔ VPS (WebRTC).

Добавочные и пароли для браузера кладёт scripts/webphone_setup.sh в файл
WEBPHONE_FILE: {"10": {"user": "wp10", "password": "..."}}. Пароль Mango туда
не попадает, он есть только в конфиге Asterisk. Пока только наша компания.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web

from tools import home_client_id

log = logging.getLogger(__name__)

UPSTREAM = os.getenv('WEBPHONE_WS', 'ws://127.0.0.1:8088/ws')
_cache: dict = {'mtime': None, 'data': {}}


def _file() -> Path:
    return Path(os.getenv('WEBPHONE_FILE', '/opt/callbot-astra/webphone.json'))


def accounts() -> dict:
    """Учётки браузера по добавочным; файл перечитываем, когда его поменял скрипт."""
    path = _file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cache['mtime']:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            log.exception('не прочитал %s', path)
            data = {}
        _cache.update(mtime=mtime, data=data if isinstance(data, dict) else {})
    return _cache['data']


async def account(pool, client_id: int, extension) -> dict | None:
    ext = str(extension or '')
    if not ext.isdigit():
        return None
    acc = accounts().get(ext)
    if not acc or not acc.get('user') or not acc.get('password'):
        return None
    if client_id != await home_client_id(pool):
        return None
    return {'user': str(acc['user']), 'password': str(acc['password'])}


def same_origin(request: web.Request) -> bool:
    """Сокет открывают только страницы самой CRM: cookie браузер отправит и с чужого сайта."""
    origin = request.headers.get('Origin', '')
    host = request.headers.get('X-Forwarded-Host') or request.host
    return origin in (f'https://{host}', f'http://{host}')


async def relay(request: web.Request) -> web.WebSocketResponse:
    """Пересылает SIP-сообщения между браузером и Asterisk как есть."""
    ws = web.WebSocketResponse(protocols=('sip',), heartbeat=25)
    await ws.prepare(request)
    try:
        async with aiohttp.ClientSession() as http, \
                http.ws_connect(UPSTREAM, protocols=('sip',), heartbeat=25) as up:
            async def pump(src, dst):
                async for msg in src:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    else:
                        break
            tasks = [asyncio.create_task(pump(ws, up)), asyncio.create_task(pump(up, ws))]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
        log.warning('телефон в браузере: Asterisk недоступен (%s)', exc)
        await ws.close(code=1011, message='asterisk unavailable'.encode())
    finally:
        if not ws.closed:
            await ws.close()
    return ws
