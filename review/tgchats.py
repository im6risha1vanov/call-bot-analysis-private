"""Чтение рабочего Telegram: переписки менеджеров с клиентами.

Бот входит в общий рабочий аккаунт как ещё одно устройство (Telethon, сессия в файле
TG_SESSION) и **только читает**: ничего не отправляет, не отмечает прочитанным, не
меняет папки. Менеджеры сами раскладывают клиентов по папкам Telegram Premium.

sync раз в TG_SYNC_MINUTES:
1. берёт папки аккаунта и чаты в них (явно добавленные и по типам: контакты, не контакты,
   группы), чей клиент — по названию папки (совпадение с именем менеджера, как в реестре);
2. дочитывает новые сообщения каждого чата: в первый раз за TG_HISTORY_DAYS дней.

Включается TG_CHATS=on. Нужны TG_API_ID и TG_API_HASH (приложение на my.telegram.org для
рабочего номера) и один вход на сервере: python -m review.tgchats login.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .presentations import match_employee

log = logging.getLogger(__name__)

HISTORY_DAYS = int(os.getenv('TG_HISTORY_DAYS', '30'))
SYNC_MINUTES = int(os.getenv('TG_SYNC_MINUTES', '30'))
MAX_DIALOGS = 1000
_last_sync: datetime | None = None


def enabled() -> bool:
    return (os.getenv('TG_CHATS', 'off').strip().lower() == 'on'
            and bool(os.getenv('TG_API_ID') and os.getenv('TG_API_HASH')))


def session_path() -> str:
    return os.getenv('TG_SESSION') or str(Path(__file__).resolve().parents[1] / 'tg-work.session')


def make_client():
    from telethon import TelegramClient
    # receive_updates=False: бот не держит поток обновлений и не выглядит активным клиентом.
    return TelegramClient(session_path(), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'],
                          receive_updates=False, device_model='Averon CRM (только чтение)')


def title_text(value) -> str:
    """Название папки: в новых версиях API это TextWithEntities, в старых строка."""
    return str(getattr(value, 'text', value) or '').strip()


def chat_kind(entity) -> str:
    name = type(entity).__name__
    if name == 'User':
        return 'user'
    if name == 'Channel' and not getattr(entity, 'megagroup', False):
        return 'channel'
    return 'group'


def in_folder(folder, peer_id: int, entity, explicit: dict) -> bool:
    """Попадает ли диалог в папку: явно добавлен или подходит по типу и не исключён."""
    if peer_id in explicit.get('include', set()):
        return True
    if peer_id in explicit.get('exclude', set()):
        return False
    kind = chat_kind(entity)
    if kind == 'user':
        if getattr(entity, 'bot', False):
            return bool(getattr(folder, 'bots', False))
        contact = bool(getattr(entity, 'contact', False))
        return bool(getattr(folder, 'contacts', False) and contact or getattr(folder, 'non_contacts', False) and not contact)
    if kind == 'group':
        return bool(getattr(folder, 'groups', False))
    return bool(getattr(folder, 'broadcasts', False))


async def folders(client) -> list[dict]:
    """Папки аккаунта: название и id чатов, добавленных явно / исключённых."""
    from telethon import utils
    from telethon.tl.functions.messages import GetDialogFiltersRequest
    result = await client(GetDialogFiltersRequest())
    out = []
    for f in getattr(result, 'filters', result):
        title = title_text(getattr(f, 'title', None))
        if not title:
            continue  # «Все чаты»
        ids = {'include': set(), 'exclude': set()}
        for key, attr in (('include', 'include_peers'), ('include', 'pinned_peers'), ('exclude', 'exclude_peers')):
            for peer in getattr(f, attr, None) or []:
                try:
                    ids[key].add(utils.get_peer_id(peer))
                except Exception:
                    continue
        out.append({'title': title, 'filter': f, 'ids': ids})
    return out


def media_kind(message) -> str | None:
    if getattr(message, 'voice', None):
        return 'voice'
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'document', None):
        return 'document'
    return type(message.media).__name__ if getattr(message, 'media', None) else None


async def sync(pool, client, client_id: int, now: datetime | None = None) -> dict:
    """Папки, чаты и новые сообщения. Возвращает счётчики для журнала."""
    now = now or datetime.now(timezone.utc)
    employees = await pool.fetch("SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager'", client_id)
    found = await folders(client)
    for f in found:
        f['extension'] = match_employee(f['title'], employees)
    stats = {'folders': len(found), 'chats': 0, 'messages': 0}
    if not found:
        return stats
    async for dialog in client.iter_dialogs(limit=MAX_DIALOGS):
        entity, peer_id = dialog.entity, dialog.id
        inside = [f for f in found if in_folder(f['filter'], peer_id, entity, f['ids'])]
        if not inside:
            continue
        managers = {f['extension'] for f in inside if f['extension']}
        row = await pool.fetchrow(
            """INSERT INTO tg_chats(client_id, peer_id, kind, title, username, phone, folders, extension)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT(client_id, peer_id) DO UPDATE SET kind=EXCLUDED.kind, title=EXCLUDED.title,
                 username=EXCLUDED.username, phone=EXCLUDED.phone, folders=EXCLUDED.folders,
                 extension=EXCLUDED.extension, updated_at=now()
               RETURNING last_message_id""",
            client_id, peer_id, chat_kind(entity), dialog.name, getattr(entity, 'username', None),
            getattr(entity, 'phone', None), [f['title'] for f in inside],
            managers.pop() if len(managers) == 1 else None)  # чат в папках двух менеджеров: не угадываем
        stats['chats'] += 1
        last_id = row['last_message_id']
        # Чтение истории не отмечает сообщения прочитанными (это делает только mark_read).
        kwargs = {'min_id': last_id} if last_id else {'offset_date': now - timedelta(days=HISTORY_DAYS)}
        newest = None
        async for m in client.iter_messages(entity, reverse=True, **kwargs):
            if not getattr(m, 'date', None):
                continue
            await pool.execute(
                """INSERT INTO tg_messages(client_id, peer_id, message_id, sent_at, outgoing, text, media)
                   VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING""",
                client_id, peer_id, m.id, m.date, bool(m.out), m.message or None, media_kind(m))
            stats['messages'] += 1
            newest = m
        if newest is not None:
            await pool.execute('UPDATE tg_chats SET last_message_id=$3, last_message_at=$4 WHERE client_id=$1 AND peer_id=$2',
                               client_id, peer_id, newest.id, newest.date)
    return stats


async def maybe_run(pool, now: datetime | None = None) -> dict | None:
    global _last_sync
    if not enabled():
        return None
    now = now or datetime.now(timezone.utc)
    if _last_sync and now - _last_sync < timedelta(minutes=SYNC_MINUTES):
        return None
    _last_sync = now
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    if client_id is None:
        return None
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.error('Telegram: сессия не авторизована, нужен вход: python -m review.tgchats login')
            return None
        stats = await sync(pool, client, client_id, now)
        log.info('Telegram: папок %s, чатов %s, новых сообщений %s', stats['folders'], stats['chats'], stats['messages'])
        return stats
    finally:
        await client.disconnect()


if __name__ == '__main__':
    # python -m review.tgchats login — один раз: вход в рабочий аккаунт (номер, код из Telegram, пароль).
    # python -m review.tgchats folders — только чтение: какие папки видит бот и сколько в них чатов.
    import asyncio
    import sys

    async def _main(command):
        from scripts.kitchen_backfill import load_env
        load_env(Path(__file__).resolve().parents[1] / '.env')
        client = make_client()
        if command == 'login':
            await client.start()
            me = await client.get_me()
            print('Вход выполнен:', me.first_name, me.phone, '· сессия', session_path())
            os.chmod(session_path(), 0o600)
        else:
            await client.connect()
            if not await client.is_user_authorized():
                print('Сессии нет: сначала python -m review.tgchats login')
                return
            found = await folders(client)
            counts = {f['title']: 0 for f in found}
            async for dialog in client.iter_dialogs(limit=MAX_DIALOGS):
                for f in found:
                    if in_folder(f['filter'], dialog.id, dialog.entity, f['ids']):
                        counts[f['title']] += 1
            for title, n in counts.items():
                print(f'папка «{title}»: чатов {n}')
            if not found:
                print('папок нет')
        await client.disconnect()

    if sys.argv[1:2] in (['login'], ['folders']):
        asyncio.run(_main(sys.argv[1]))
