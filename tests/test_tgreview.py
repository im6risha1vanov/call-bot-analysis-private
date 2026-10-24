"""Переписка Telegram в сделках: номера из сообщений, проверка разбора, какие чаты разбираются."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg

from review import tgreview as T

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
NOW = datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
MSK = ZoneInfo('Europe/Moscow')


def m(text, outgoing=False, hours=1, media=None):
    return {'text': text, 'outgoing': outgoing, 'sent_at': NOW - timedelta(hours=hours), 'media': media}


class PureTests(unittest.TestCase):
    def test_phones_in(self):
        self.assertEqual(T.phones_in(['Мой номер +7 (999) 123-45-67', 'или 8-912-345-67-89, ещё 9161234567',
                                      'ИНН 7707083893 и 770708389312', 'повторю +79991234567']),
                         ['79991234567', '79123456789', '79161234567'])
        self.assertEqual(T.phones_in([None, 'счёт 40702810900000000001']), [])

    def test_validate(self):
        messages = [m('Пришлите КП на почту'), m('Отправил КП, посмотрите', True), m('Давайте в пятницу созвонимся')]
        self.assertEqual(T.validate({'kind': 'not_client', 'rop_line': 'Личное.'}, messages),
                         {'kind': 'not_client', 'reviewed': False, 'rop_line': 'Личное.'})
        data = T.validate({
            'kind': 'client', 'stage_after': 'decision_date', 'temperature': 'boiling',
            'stage_evidence': [{'from': 'manager', 'quote': 'Отправил КП, посмотрите'}],
            'key_moment': {'category': 'weird', 'from': 'client', 'quote': 'Отправил КП', 'at': '24.10 20:00'},
            'positives': [{'from': 'manager', 'quote': 'Отправил КП, посмотрите'}, {'from': 'manager', 'quote': 'выдумка тут'}],
            'contract': {'missing': ['inn', 'mood']}}, messages)
        # подтверждение этапа — только сообщением клиента; цитата не той стороны снимается
        self.assertEqual((data['stage_after'], data['moved'], data['temperature']), ('none', False, 'warm'))
        self.assertEqual(data['key_moment'], {'category': 'other', 'from': 'client', 'quote': None, 'at': None})
        self.assertEqual((len(data['positives']), data['contract']['missing']), (1, ['inn']))
        ok = T.validate({'kind': 'client', 'stage_after': 'decision_date', 'temperature': 'hot',
                         'stage_evidence': [{'from': 'client', 'quote': 'в пятницу созвонимся'}],
                         'key_moment': {'category': 'no_next_step'}}, messages)
        self.assertEqual((ok['stage_after'], ok['moved'], ok['key_moment']), ('decision_date', True, None))

    def test_build_input(self):
        chat = {'title': 'Анна', 'username': 'anna_s'}
        text = T.build_input(chat, [m('Здравствуйте', hours=5), m(None, True, 4, 'document'), m('Спасибо', hours=2)],
                             MSK, NOW, None, ['20.10: Холодный звонок, перезвон в пятницу.'], 0)
        self.assertIn('Клиент в Telegram: Анна (@anna_s)', text)
        self.assertIn('Последнее сообщение: от клиента, 24.10 19:00 (2 ч назад)', text)
        self.assertIn('[24.10 17:00 Менеджер] [файл]', text)
        self.assertIn('20.10: Холодный звонок', text)
        long = T.build_input(chat, [m('а' * 20000, hours=3), m('б' * 20000)], MSK, NOW, 'Первый', [], 5)
        self.assertIn('(раньше было ещё 6 сообщений', long)
        self.assertNotIn('ааа', long)


@unittest.skipUnless(DSN, 'нужна тестовая база')
class DBTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute('ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT')
        for name in ('008_call_reviews.sql', '022_presentations.sql', '023_tg_chats.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients, tg_messages, astra_daily_spend CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'101',123,'manager','Первый')")
        T._fails.clear()

    async def asyncTearDown(self):
        await self.pool.close()

    async def chat(self, peer, texts, phone=None, quiet_hours=5):
        await self.pool.execute("INSERT INTO tg_chats(client_id,peer_id,kind,title,phone,last_message_id,last_message_at) "
                                "VALUES(1,$1,'user','Клиент',$2,$3,$4)", peer, phone, len(texts), NOW - timedelta(hours=quiet_hours))
        for i, (out, text) in enumerate(texts, 1):
            await self.pool.execute('INSERT INTO tg_messages VALUES(1,$1,$2,$3,$4,$5,NULL)', peer, i,
                                    NOW - timedelta(hours=quiet_hours + len(texts) - i), out, text)

    async def test_links_only_known_numbers(self):
        await self.pool.execute("INSERT INTO calls(client_id,extension,call_started_at,client_number) VALUES"
                                "(1,'101',now(),'+7 999 000-00-01'),(1,'101',now(),'79990000002')")
        await self.chat(1, [(False, 'Мой номер 8 999 000-00-01')])
        await self.chat(2, [(False, 'Звоните 89123456789')])  # такого номера нет ни в звонках, ни в базе
        await self.chat(3, [(False, 'Я 89990000001, директор 89990000002')])  # два знакомых: не угадываем
        await self.chat(4, [(True, 'Наш номер 89990000002')])  # номер написал менеджер
        self.assertEqual(await T.link_chats(self.pool, 1), 1)
        rows = await self.pool.fetch('SELECT peer_id, phone, analysis FROM tg_chats ORDER BY peer_id')
        self.assertEqual([(r['peer_id'], r['phone']) for r in rows], [(1, '79990000001'), (2, None), (3, None), (4, None)])
        self.assertEqual(json.loads(rows[0]['analysis']), {'link': {'by': 'text'}})

    async def test_reviews_quiet_chats_once(self):
        calls = []

        async def fake(system, user, max_tokens):
            calls.append(user)
            return {'kind': 'client', 'temperature': 'cold', 'stage_after': 'none',
                    'key_moment': {'category': 'no_followup', 'from': 'manager', 'quote': 'Отправил КП, посмотрите'},
                    'rop_line': 'КП отправлено, клиент молчит.'}, 0.5

        await self.chat(1, [(False, 'Пришлите КП'), (True, 'Отправил КП, посмотрите')], phone='79990000001')
        await self.pool.execute("UPDATE tg_chats SET analysis='{\"link\": {\"by\": \"contact\"}}' WHERE peer_id=1")
        await self.chat(2, [(False, 'Здравствуйте')], quiet_hours=1)  # переписка ещё идёт
        await self.chat(3, [(True, 'Добрый день, это Averon')])  # клиент ничего не написал
        stats = await T.maybe_run(self.pool, NOW, call=fake)
        self.assertEqual(stats, {'linked': 0, 'reviewed': 1})
        self.assertIn('Менеджер: рабочий аккаунт', calls[0])
        self.assertIn('Клиент] Пришлите КП', calls[0])
        row = await self.pool.fetchrow('SELECT analysis, analyzed_at FROM tg_chats WHERE peer_id=1')
        data = json.loads(row['analysis'])
        self.assertEqual((data['temperature'], data['key_moment']['quote'], data['link'], data['messages']),
                         ('cold', 'Отправил КП, посмотрите', {'by': 'contact'}, 2))
        self.assertEqual(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend'), 0.5)
        # без новых сообщений второй раз не разбирается
        self.assertEqual(await T.process(self.pool, 1, NOW + timedelta(hours=1), call=fake), 0)

        async def broken(*a):
            raise RuntimeError('нет ответа')

        await self.pool.execute('UPDATE tg_chats SET analyzed_at=NULL WHERE peer_id=1')
        for _ in range(T.MAX_FAILS + 1):
            self.assertEqual(await T.process(self.pool, 1, NOW, call=broken), 0)
        self.assertEqual(T._fails[(1, 1, 2)], T.MAX_FAILS)  # после трёх неудач ждёт новых сообщений

    async def test_not_client_rechecked_rarely(self):
        async def fake(*a):
            return {'kind': 'not_client', 'rop_line': 'Личное.'}, 0.1

        await self.chat(1, [(False, 'Купи хлеба')])
        await self.chat(2, [(False, 'Скидки на всё')])
        await self.pool.execute("""UPDATE tg_chats SET analysis='{"kind": "not_client"}',
                                     analyzed_at=$1::timestamptz - (peer_id * 2 || ' days')::interval""", NOW - timedelta(hours=6))
        self.assertEqual(await T.process(self.pool, 1, NOW, call=fake), 1)  # только чат, проверенный 4 дня назад
        self.assertIsNotNone(await self.pool.fetchval("SELECT analyzed_at FROM tg_chats WHERE peer_id=2 AND analyzed_at > $1", NOW - timedelta(minutes=1)))
