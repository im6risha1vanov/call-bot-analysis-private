"""Основной бот из CRM: имя бота для ссылок-приглашений и короткие уведомления через Bot API."""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)
_username: str | None = None


async def bot_username() -> str | None:
    """Имя основного бота: BOT_USERNAME из .env или getMe по BOT_TOKEN (один раз)."""
    global _username
    name = os.getenv('BOT_USERNAME', '').strip().lstrip('@')
    if name:
        return name
    if _username:
        return _username
    token = os.getenv('BOT_TOKEN')
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f'https://api.telegram.org/bot{token}/getMe')
        _username = r.json()['result']['username']
    except Exception:
        log.warning('не удалось узнать имя бота через getMe')
    return _username


async def send(chat: int, text: str) -> bool:
    token = os.getenv('BOT_TOKEN')
    if not chat or not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat, 'text': text})
        return r.status_code == 200
    except Exception:
        log.warning('не удалось написать в Telegram chat=%s', chat)
        return False
