"""Переписка через Telegram Business: что сохраняется и что бот ничего не отправляет."""
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
from aiogram import Dispatcher
from aiogram.types import Chat, Message, User, Voice

from review import tgbusiness as B

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
WHEN = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
CLIENT = Chat(id=555, type='private', first_name='Анна', last_name='Смирнова', username='anna_s')
CLIENT_USER = User(id=555, is_bot=False, first_name='Анна')
WORK = User(id=999, is_bot=False, first_name='Averon')


def msg(message_id, sender, text=None, chat=CLIENT, **kw):
    return Message(message_id=message_id, date=WHEN, chat=chat, from_user=sender, text=text,
                   business_connection_id='bc1', **kw)


class RouterTests(unittest.TestCase):
    def test_update_types(self):
        dp = Dispatcher()
        dp.include_router(B.router)
        self.assertTrue({'business_message', 'edited_business_message', 'business_connection'}
                        <= set(dp.resolve_used_update_types()))

    def test_media_kind(self):
        voice = Voice(file_id='f', file_unique_id='u', duration=3)
        self.assertEqual(B.media_kind(msg(1, CLIENT_USER, voice=voice)), 'voice')
        self.assertIsNone(B.media_kind(msg(2, CLIENT_USER, 'текст')))


@unittest.skipUnless(DSN, 'нужна тестовая база')
class SaveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute((root / 'migrations' / '023_tg_chats.sql').read_text())
        await self.pool.execute('TRUNCATE clients, tg_messages CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        B._client_id = None

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_saves_both_directions_and_edits(self):
        self.assertTrue(await B.save(self.pool, msg(10, CLIENT_USER, 'Здравствуйте, пришлите КП')))
        self.assertTrue(await B.save(self.pool, msg(11, WORK, 'Отправил, посмотрите')))
        self.assertTrue(await B.save(self.pool, msg(11, WORK, 'Отправил КП, посмотрите')))  # исправил
        rows = await self.pool.fetch('SELECT message_id, outgoing, text FROM tg_messages ORDER BY message_id')
        self.assertEqual([tuple(r) for r in rows],
                         [(10, False, 'Здравствуйте, пришлите КП'), (11, True, 'Отправил КП, посмотрите')])
        chat = await self.pool.fetchrow('SELECT * FROM tg_chats')
        self.assertEqual((chat['peer_id'], chat['title'], chat['username'], chat['last_message_id'], chat['folders']),
                         (555, 'Анна Смирнова', 'anna_s', 11, []))

    async def test_ignores_groups_and_sends_nothing(self):
        group = Chat(id=-100, type='supergroup', title='Чат')
        self.assertFalse(await B.save(self.pool, msg(1, CLIENT_USER, 'привет', chat=group)))
        bot = AsyncMock()
        B.POOL = self.pool
        try:
            await B.on_message(msg(12, CLIENT_USER, 'Добрый день').as_(bot))
        finally:
            B.POOL = None
        self.assertEqual(bot.mock_calls, [])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM tg_messages'), 1)
