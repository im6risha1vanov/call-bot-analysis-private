"""Переписка рабочего Telegram через бота Telegram Business: без ключей my.telegram.org.

Рабочий аккаунт (нужен Premium) подключает нашего бота: Настройки → Telegram Business → Чат-боты.
Telegram присылает боту новые сообщения личных чатов аккаунта, и от клиентов, и от менеджеров.
Бот только складывает их в tg_chats / tg_messages, те же таблицы, что у чтения через Telethon
(tgchats.py): ничего не отправляет, не отмечает прочитанным, не удаляет. Истории до подключения
нет, групп и папок бот не видит, поэтому чей это клиент, решается потом по сделке.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import BusinessConnection, Message

log = logging.getLogger(__name__)
router = Router(name='tg-business')
POOL = None  # asyncpg-пул, его задаёт demo_bot при запуске
_client_id: int | None = None

MEDIA = ('voice', 'video_note', 'audio', 'video', 'animation', 'photo', 'sticker', 'document', 'contact', 'location')


def media_kind(message: Message) -> str | None:
    return next((kind for kind in MEDIA if getattr(message, kind, None)), None)


def chat_title(message: Message) -> str | None:
    chat = message.chat
    return ' '.join(p for p in (chat.first_name, chat.last_name) if p) or chat.title or chat.username


async def save(pool, message: Message) -> bool:
    """Сообщение (новое или исправленное) в tg_messages, чат клиента в tg_chats."""
    global _client_id
    if message.chat.type != 'private':
        return False
    if _client_id is None:
        _client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
        if _client_id is None:
            return False
    peer_id = message.chat.id
    # В личном чате сообщение клиента идёт от него самого, остальное написал рабочий аккаунт.
    outgoing = not message.from_user or message.from_user.id != peer_id
    await pool.execute(
        """INSERT INTO tg_chats(client_id, peer_id, kind, title, username, last_message_id, last_message_at)
           VALUES($1,$2,'user',$3,$4,$5,$6)
           ON CONFLICT(client_id, peer_id) DO UPDATE SET title=coalesce(EXCLUDED.title, tg_chats.title),
             username=coalesce(EXCLUDED.username, tg_chats.username),
             last_message_id=greatest(tg_chats.last_message_id, EXCLUDED.last_message_id),
             last_message_at=greatest(tg_chats.last_message_at, EXCLUDED.last_message_at), updated_at=now()""",
        _client_id, peer_id, chat_title(message), message.chat.username, message.message_id, message.date)
    await pool.execute(
        """INSERT INTO tg_messages(client_id, peer_id, message_id, sent_at, outgoing, text, media)
           VALUES($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT(client_id, peer_id, message_id) DO UPDATE SET text=EXCLUDED.text, media=EXCLUDED.media""",
        _client_id, peer_id, message.message_id, message.date, outgoing, message.text or message.caption,
        media_kind(message))
    return True


@router.business_connection()
async def on_connection(connection: BusinessConnection):
    rights = connection.rights
    can_reply = rights.can_reply if rights is not None else connection.can_reply
    log.info('Telegram Business: аккаунт %s (%s) %s, право отвечать: %s', connection.user.id,
             connection.user.username or connection.user.first_name,
             'подключил бота' if connection.is_enabled else 'отключил бота', 'да' if can_reply else 'нет')
    if connection.is_enabled and can_reply:
        log.warning('Telegram Business: у бота есть право отвечать клиентам. Бот им не пользуется, '
                    'но его лучше снять в настройках чат-бота рабочего аккаунта.')


@router.business_message()
@router.edited_business_message()
async def on_message(message: Message):
    if POOL is None:
        return
    try:
        await save(POOL, message)
    except Exception:
        log.exception('Telegram Business: сообщение %s в чате %s не сохранено', message.message_id, message.chat.id)


if __name__ == '__main__':
    # python -m review.tgbusiness status — сколько переписки пришло через Telegram Business.
    import asyncio
    import os
    import sys
    from pathlib import Path

    async def _status():
        import asyncpg
        from scripts.kitchen_backfill import load_env
        load_env(Path(__file__).resolve().parents[1] / '.env')
        pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=1)
        try:
            # Чаты из Telethon всегда лежат в папках; у чатов из Telegram Business папок нет.
            row = await pool.fetchrow(
                """SELECT count(DISTINCT c.peer_id) AS chats, count(m.message_id) AS messages,
                          count(m.message_id) FILTER (WHERE m.sent_at > now() - interval '1 day') AS day,
                          max(m.sent_at) AS last
                     FROM tg_chats c LEFT JOIN tg_messages m ON m.client_id=c.client_id AND m.peer_id=c.peer_id
                    WHERE c.folders = '{}'""")
            print(f"чатов {row['chats']}, сообщений {row['messages']}, за сутки {row['day']}, "
                  f"последнее {row['last']:%d.%m %H:%M} UTC" if row['last'] else 'сообщений пока нет')
        finally:
            await pool.close()

    if sys.argv[1:2] == ['status']:
        asyncio.run(_status())
