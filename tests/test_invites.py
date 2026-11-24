"""Ссылка-приглашение в бота. Данные вымышленные."""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')

import invites

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')


class CodeTests(unittest.TestCase):
    def test_code_from_start_text(self):
        self.assertEqual(invites.code_from('/start inv_1c94836fdc741a4931e7'), '1c94836fdc741a4931e7')
        for text in ('/start', '/start train_5', '/start inv_x', '/start inv_abc;drop', None, '/start inv_1234567890 x'):
            self.assertIsNone(invites.code_from(text), text)


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class AcceptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute((root / 'migrations/013_employee_invites.sql').read_text())
        await self.pool.execute('TRUNCATE clients, employee_invites CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'head:ivan',NULL,'head','Ivan'),(1,'10',555,'manager','Лузин')")
        await self.pool.execute("INSERT INTO employee_invites(code, client_id, extension) VALUES('abcdef1234',1,'head:ivan')")

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_link_binds_once_and_takes_telegram_name(self):
        row, problem = await invites.accept(self.pool, 'abcdef1234', 777, 'Иван Петров')
        self.assertIsNone(problem)
        self.assertEqual((row['role'], row['full_name']), ('head', 'Иван Петров'))
        self.assertEqual(await self.pool.fetchval("SELECT telegram_user_id FROM employees WHERE extension='head:ivan'"), 777)
        row, problem = await invites.accept(self.pool, 'abcdef1234', 888, 'Чужой')
        self.assertIsNone(row)
        self.assertIn('уже использована', problem)

    async def test_expired_unknown_and_already_connected(self):
        self.assertIn('неверна', (await invites.accept(self.pool, 'nope123456', 777, 'x'))[1])
        self.assertIn('уже подключены', (await invites.accept(self.pool, 'abcdef1234', 555, 'x'))[1])
        await self.pool.execute("UPDATE employee_invites SET expires_at=now() - interval '1 minute'")
        self.assertIn('истёк', (await invites.accept(self.pool, 'abcdef1234', 777, 'x'))[1])
        self.assertIsNone(await self.pool.fetchval("SELECT telegram_user_id FROM employees WHERE extension='head:ivan'"))

    async def test_start_command_with_link(self):
        import demo_bot
        message = SimpleNamespace(text='/start inv_abcdef1234', chat=SimpleNamespace(type='private', id=777),
                                  from_user=SimpleNamespace(id=777, first_name='Иван', last_name=None, username=None),
                                  answer=AsyncMock())
        with patch.object(demo_bot, 'PG_POOL', self.pool), patch.object(demo_bot, 'help_command', AsyncMock()) as help_:
            await demo_bot.start_command(message)
        self.assertIn('вы подключены как руководитель', message.answer.await_args_list[0].args[0])
        help_.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
