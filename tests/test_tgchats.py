"""Чтение рабочего Telegram: папки, чей клиент, дочитывание сообщений. Клиент Telegram поддельный."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg

from review import tgchats as T

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


class User(SimpleNamespace):
    pass


class Channel(SimpleNamespace):
    pass


def folder(title, include=(), exclude=(), **flags):
    return SimpleNamespace(title=SimpleNamespace(text=title), include_peers=list(include), pinned_peers=[],
                           exclude_peers=list(exclude), **flags)


class FakeClient:
    def __init__(self, filters, dialogs, messages):
        self.filters, self.dialogs, self.messages, self.calls = filters, dialogs, messages, []

    async def __call__(self, request):
        return SimpleNamespace(filters=self.filters)

    async def iter_dialogs(self, limit=None):
        for d in self.dialogs:
            yield d

    async def iter_messages(self, entity, reverse=False, min_id=None, offset_date=None):
        self.calls.append({'peer': entity.id, 'min_id': min_id, 'offset_date': offset_date})
        for m in self.messages.get(entity.id, []):
            if (min_id and m.id <= min_id) or (offset_date and m.date < offset_date):
                continue
            yield m


def msg(i, days_ago, out, text, **extra):
    return SimpleNamespace(id=i, date=NOW - timedelta(days=days_ago), out=out, message=text, media=None, **extra)


class FolderTests(unittest.TestCase):
    def test_membership(self):
        contact = User(id=1, contact=True, bot=False)
        stranger = User(id=2, contact=False, bot=False)
        group = Channel(id=-1003, megagroup=True)
        by_type = folder('Лузин', contacts=True, non_contacts=False)
        self.assertTrue(T.in_folder(by_type, 1, contact, {'include': set(), 'exclude': set()}))
        self.assertFalse(T.in_folder(by_type, 2, stranger, {'include': set(), 'exclude': set()}))
        self.assertFalse(T.in_folder(by_type, 1, contact, {'include': set(), 'exclude': {1}}))
        self.assertTrue(T.in_folder(folder('x'), 2, stranger, {'include': {2}, 'exclude': set()}))
        self.assertFalse(T.in_folder(folder('x'), -1003, group, {'include': set(), 'exclude': set()}))
        self.assertEqual((T.chat_kind(contact), T.chat_kind(group)), ('user', 'group'))
        self.assertEqual(T.title_text(SimpleNamespace(text=' Козлов ')), 'Козлов')


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class KeysTests(unittest.TestCase):
    def test_save_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}):
            env, backups = Path(tmp) / '.env', Path(tmp) / 'backups'
            backups.mkdir()
            env.write_text('DATABASE_URL=x\nTG_API_ID=1\nTG_API_HASH=old\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                T.save_keys(env, '12a', 'f' * 32, backups)
            with self.assertRaises(ValueError):
                T.save_keys(env, '123', 'xyz', backups)
            T.save_keys(env, '123', 'ab' * 16, backups)
            self.assertEqual(env.read_text(encoding='utf-8'),
                             'DATABASE_URL=x\nTG_API_ID=123\nTG_API_HASH=' + 'ab' * 16 + '\n')
            self.assertIn('TG_API_HASH=old', (backups / 'env.before-tg').read_text(encoding='utf-8'))
            self.assertEqual(os.environ['TG_API_ID'], '123')


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute((root / 'migrations' / '023_tg_chats.sql').read_text())
        await self.pool.execute('TRUNCATE clients, tg_messages CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'12',123,'manager','Козлов Илья'),(1,'10',124,'manager','Ефим Лузин')")

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_sync_reads_folders_and_new_messages(self):
        anna = User(id=501, contact=True, bot=False, username='anna', phone='79990001111')
        oleg = User(id=502, contact=False, bot=False, username=None, phone=None)
        both = User(id=503, contact=False, bot=False, username=None, phone=None)
        other = User(id=504, contact=True, bot=False, username=None, phone=None)  # вне папок
        dialogs = [SimpleNamespace(id=u.id, entity=u, name=n) for u, n in
                   ((anna, 'Анна Ромашка'), (oleg, 'Олег'), (both, 'Общий'), (other, 'Мама'))]
        messages = {501: [msg(1, 40, True, 'старое'), msg(2, 3, True, 'Отправил КП'), msg(3, 1, False, 'Посмотрим', voice=True)],
                    502: [msg(7, 2, False, 'Здравствуйте')]}
        filters = [SimpleNamespace(title=None),  # «Все чаты»
                   folder('Илья Козлов', include=['p501', 'p503']),
                   folder('Лузин', include=['p502', 'p503'])]
        client = FakeClient(filters, dialogs, messages)
        with patch('telethon.utils.get_peer_id', lambda p: int(p[1:])):
            stats = await T.sync(self.pool, client, 1, NOW)
            self.assertEqual(stats, {'folders': 2, 'chats': 3, 'messages': 3})  # старше 30 дней и «Мама» не читаются
            chats = {r['peer_id']: r for r in await self.pool.fetch('SELECT * FROM tg_chats')}
            self.assertEqual((chats[501]['extension'], chats[501]['folders'], chats[501]['last_message_id']), ('12', ['Илья Козлов'], 3))
            self.assertEqual(chats[502]['extension'], '10')
            self.assertIsNone(chats[503]['extension'])  # в папках двух менеджеров
            self.assertNotIn(504, chats)
            voice = await self.pool.fetchrow('SELECT outgoing, text, media FROM tg_messages WHERE peer_id=501 AND message_id=3')
            self.assertEqual(tuple(voice), (False, 'Посмотрим', 'voice'))

            messages[501].append(msg(4, 0, True, 'Напомню завтра'))
            client.calls.clear()
            stats = await T.sync(self.pool, client, 1, NOW)
        self.assertEqual(stats['messages'], 1)
        self.assertEqual(next(c for c in client.calls if c['peer'] == 501)['min_id'], 3)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM tg_messages'), 4)

    async def test_disabled_without_keys(self):
        with patch.dict(os.environ, {'TG_CHATS': 'on', 'TG_API_ID': '', 'TG_API_HASH': ''}):
            self.assertFalse(T.enabled())
            self.assertIsNone(await T.maybe_run(self.pool))
