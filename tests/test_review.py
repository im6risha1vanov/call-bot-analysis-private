"""Разбор по результату: проверка ответа модели и тексты. Данные вымышленные."""
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')

from review import engine, render, store
from tools import Actor, Forbidden

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')

TRANSCRIPT = '\n'.join([
    '[00:01 Спикер 0] Здравствуйте, Иван, компания Аверон, по поводу контактов клиентов.',
    '[00:05 Спикер 1] Я сейчас очень занят, наберите через месяц.',
    '[00:09 Спикер 0] Хорошо, до свидания.',
])
CALLBACK = '\n'.join([
    '[00:01 Спикер 0] Добрый день, это Аверон, мы обсуждали контакты на прошлой неделе.',
    '[00:06 Спикер 1] Да, давайте в пятницу в одиннадцать созвонимся.',
    '[00:10 Спикер 0] Договорились, наберу в пятницу в одиннадцать.',
])
ROLES = {'manager': 'Спикер 0', 'client': 'Спикер 1', 'other': None, 'merged': False}
LOST = {
    'roles': ROLES, 'kind': 'cold_first', 'outcome': [], 'outcome_evidence': [], 'success': False, 'chance': 'normal',
    'key_moment': {'category': 'no_callback_date', 'timecode': '00:05', 'speaker': 'client',
                   'quote': 'наберите через месяц', 'what_happened': 'Клиент отложил разговор, день не назначен.',
                   'say_instead': 'Понимаю, давайте я наберу девятого ноября до обеда, удобно?'},
    'other_misses': [], 'positives': [],
    'client': {'next_step': {'type': 'callback', 'date': '2026-11-09', 'date_is_estimate': True, 'time': None, 'raw': 'через месяц'}},
    'manager_text': 'Клиент занят и попросил набрать через месяц, день не закрепили.\n[00:05] «наберите через месяц»\nКак можно было: «Давайте я наберу девятого ноября до обеда?»',
    'rop_line': 'Холодный. Без результата, шанс был. Принял «занят», день перезвона не закрепил.',
    'details': 'Иван занят.\nКлючевой момент: «наберите через месяц».',
}
WON = {
    'roles': ROLES, 'kind': 'repeat', 'outcome': ['callback_date'], 'success': True, 'chance': None, 'key_moment': None,
    'outcome_evidence': [{'outcome': 'callback_date', 'timecode': '00:06', 'speaker': 'client',
                          'quote': 'давайте в пятницу в одиннадцать созвонимся'}],
    'positives': [{'timecode': '00:10', 'speaker': 'manager', 'quote': 'наберу в пятницу в одиннадцать', 'why': 'Закрепил день и время.'}],
    'manager_text': 'Закрепил созвон в пятницу в 11:00, отлично.', 'rop_line': 'Повторный. Перезвон в пятницу 11:00.',
    'details': 'Клиент согласился на созвон в пятницу.',
}
META = {'manager': 'Учебный менеджер', 'client_number': '79990001122',
        'call_started_at': datetime(2026, 10, 7, 14, 40), 'duration_seconds': 94}


class ValidateTests(unittest.TestCase):
    def test_lost_call_keeps_verified_key_moment(self):
        data = engine.validate(copy.deepcopy(LOST), TRANSCRIPT)
        self.assertTrue(data['reviewed'])
        self.assertFalse(data['success'])
        self.assertEqual(data['key_moment']['quote'], 'наберите через месяц')
        self.assertFalse(data['text_unverified'])
        self.assertEqual(engine.immediate(data), {'manager': True, 'rop': False})
        self.assertEqual(engine.level(data), '❌')

    def test_client_words_attributed_to_manager_are_rejected(self):
        bad = copy.deepcopy(LOST)
        bad['key_moment']['speaker'] = 'manager'
        data = engine.validate(bad, TRANSCRIPT)
        self.assertIsNone(data['key_moment']['quote'])
        self.assertTrue(data['text_unverified'])  # готовый текст цитировал непроверенное
        self.assertNotIn('Ты: «наберите', render.manager_message(META, data))

    def test_merged_tracks_check_text_not_label(self):
        merged = copy.deepcopy(LOST)
        merged['roles'] = {'manager': 'Спикер 0', 'client': 'Спикер 0 (речь не разделена)', 'merged': True}
        flat = TRANSCRIPT.replace('Спикер 1', 'Спикер 0')
        self.assertEqual(engine.validate(merged, flat)['key_moment']['quote'], 'наберите через месяц')

    def test_invented_quote_and_wrong_time_are_dropped(self):
        for change in ({'quote': 'мне это совсем не интересно'}, {'timecode': '01:30'}):
            bad = copy.deepcopy(LOST)
            bad['key_moment'].update(change)
            self.assertIsNone(engine.validate(bad, TRANSCRIPT)['key_moment']['quote'])

    def test_success_needs_confirming_reply(self):
        data = engine.validate(copy.deepcopy(WON), CALLBACK)
        self.assertTrue(data['success'])
        self.assertEqual(engine.immediate(data), {'manager': False, 'rop': False})
        claimed = copy.deepcopy(WON)
        claimed['outcome_evidence'][0]['quote'] = 'да, записывайте меня на встречу'
        data = engine.validate(claimed, CALLBACK)
        self.assertFalse(data['success'])
        self.assertEqual(data['chance'], 'normal')
        self.assertTrue(data['text_unverified'])

    def test_single_word_evidence_needs_exact_time(self):
        one = copy.deepcopy(WON)
        one['outcome_evidence'][0].update(quote='пятницу', timecode=None)
        self.assertFalse(engine.validate(one, CALLBACK)['success'])
        one['outcome_evidence'][0]['timecode'] = '00:06'
        self.assertTrue(engine.validate(one, CALLBACK)['success'])

    def test_hot_loss_goes_to_rop_too(self):
        hot = copy.deepcopy(LOST); hot['chance'] = 'hot'
        self.assertEqual(engine.immediate(engine.validate(hot, TRANSCRIPT)), {'manager': True, 'rop': True})

    def test_no_chance_is_not_sent_immediately(self):
        none = copy.deepcopy(LOST); none['chance'] = 'none'
        data = engine.validate(none, TRANSCRIPT)
        self.assertIsNone(data['key_moment'])
        self.assertEqual(engine.immediate(data), {'manager': False, 'rop': False})
        self.assertEqual(engine.level(data), '⚠️')

    def test_other_product_and_no_conversation_are_not_reviewed(self):
        for kind in ('other_product', 'no_conversation', 'not_sales'):
            data = engine.validate({**copy.deepcopy(LOST), 'kind': kind}, TRANSCRIPT)
            self.assertFalse(data['reviewed'])
            self.assertIsNone(engine.level(data))
            self.assertEqual(engine.immediate(data), {'manager': False, 'rop': False})

    def test_mode_switch_defaults_to_legacy(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CALL_REVIEW_MODE', None)
            self.assertFalse(engine.enabled())
            os.environ['CALL_REVIEW_MODE'] = 'v2'
            self.assertTrue(engine.enabled())
            os.environ.pop('CALL_REVIEW_MODE')

    def test_prompt_has_no_doc_header(self):
        self.assertTrue(engine.PROMPT.startswith('Ты опытный руководитель'))


class RenderTests(unittest.TestCase):
    def test_manager_message_is_short_and_escaped(self):
        data = engine.validate({**copy.deepcopy(LOST), 'manager_text': 'Строка <b>1</b>\n2\n3\n4\n5\n6\n7\n8'}, TRANSCRIPT)
        text = render.manager_message(META, data)
        self.assertIn('&lt;b&gt;1&lt;/b&gt;', text)
        self.assertLessEqual(len(text.splitlines()), 1 + render.MAX_LINES)
        self.assertTrue(text.startswith('❌ · 07.10 14:40 · 1:34'))
        for word in ('балл', 'критери', 'шкал'):
            self.assertNotIn(word, text.lower())

    def test_rop_and_details_carry_manager_name(self):
        data = engine.validate(copy.deepcopy(LOST), TRANSCRIPT)
        self.assertIn('Учебный менеджер', render.rop_message(META, data))
        self.assertIn('Учебный менеджер', render.details_message(META, data))
        self.assertEqual(render.keyboard(5).inline_keyboard[0][0].callback_data, 'rv:d:5')

    def test_fallback_text_uses_only_verified_fields(self):
        bad = copy.deepcopy(LOST); bad['key_moment']['quote'] = 'выдуманная фраза клиента'
        bad['manager_text'] = 'выдуманная фраза клиента'
        text = render.manager_message(META, engine.validate(bad, TRANSCRIPT))
        self.assertNotIn('выдуманная', text)
        self.assertIn('девятого ноября', text)

    def test_daily_summaries(self):
        rows = [{'call_id': 1, 'extension': '101', 'meta': META, 'data': engine.validate(copy.deepcopy(LOST), TRANSCRIPT)},
                {'call_id': 2, 'extension': '101', 'meta': META, 'data': engine.validate(copy.deepcopy(WON), CALLBACK)}]
        manager = render.manager_summary('Учебный менеджер', rows, tomorrow='2026-11-09')
        self.assertIn('Разговоров: 2, с результатом: 1', manager)
        self.assertIn('Закрепил день и время.', manager)
        self.assertIn('не закреплён день перезвона', manager)
        self.assertIn('Договорённости на завтра', manager)
        text, buttons = render.rop_summary(rows)
        self.assertIn('потеряно с шансом: 1', text)
        self.assertEqual([b[0] for b in buttons], [1])
        self.assertIsNone(render.manager_summary('x', []))
        self.assertIsNone(render.rop_summary([]))


@unittest.skipUnless(DSN, 'Requires isolated PostgreSQL')
class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS duration_seconds INTEGER DEFAULT 90; ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT; ALTER TABLE employees ADD COLUMN IF NOT EXISTS id BIGSERIAL")
        await self.pool.execute((root / 'migrations/007_call_reviews.sql').read_text())
        await self.pool.execute('TRUNCATE clients CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'101',123,'manager','Первый'),(1,'102',124,'manager','Второй'),(1,'',900,'owner','Владелец'),(1,'head',901,'head','Руководитель')")
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))

    async def asyncTearDown(self):
        await self.pool.close()

    async def add(self, data, transcript=TRANSCRIPT, when='2026-10-07T11:40:00+00:00'):
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,'79990001122') RETURNING id",
            transcript, datetime.fromisoformat(when))
        call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
        async with self.pool.acquire() as conn:
            await store.save(conn, call, engine.validate(copy.deepcopy(data), transcript), 12.5)
        return call_id

    async def test_lost_call_goes_to_manager_once(self):
        call_id = await self.add(LOST)
        self.assertEqual(await store.deliver_now(self.pool, self.bot, call_id), {'manager': 123})
        self.assertEqual(await store.deliver_now(self.pool, self.bot, call_id), {})
        self.assertEqual(self.bot.send_message.await_count, 1)
        text = self.bot.send_message.await_args.args[1]
        self.assertTrue(text.startswith('❌ · 07.10 14:40'))  # время клиента, не UTC

    async def test_hot_loss_also_goes_to_heads_and_owner(self):
        call_id = await self.add({**LOST, 'chance': 'hot'})
        sent = await store.deliver_now(self.pool, self.bot, call_id)
        self.assertEqual(sent, {'manager': 123, 'rop': [900, 901]})
        self.assertIn('Первый', self.bot.send_message.await_args_list[-1].args[1])

    async def test_success_waits_for_evening(self):
        call_id = await self.add(WON, CALLBACK)
        self.assertEqual(await store.deliver_now(self.pool, self.bot, call_id), {})
        self.bot.send_message.assert_not_awaited()

    async def test_access_and_day_rows(self):
        call_id = await self.add(LOST)
        await store.access(self.pool, Actor(123, 1, 'manager', '101'), call_id)
        await store.access(self.pool, Actor(901, 1, 'head', None), call_id)
        with self.assertRaises(Forbidden):
            await store.access(self.pool, Actor(124, 1, 'manager', '102'), call_id)
        start = datetime(2026, 10, 7, tzinfo=timezone.utc)
        rows = await store.day_rows(self.pool, 1, start, start + timedelta(days=1))
        self.assertEqual([r['call_id'] for r in rows], [call_id])
        self.assertEqual(rows[0]['meta']['manager'], 'Первый')
        previous = await store.previous(self.pool, 1, '79990001122', call_id + 1)
        self.assertEqual(previous['rop_line'], LOST['rop_line'])

    async def test_queue_handler_uses_one_review_instead_of_score(self):
        import handlers.analyze_call as handler
        await self.pool.execute("""
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS vpbx_api_key_enc TEXT;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS vpbx_api_salt_enc TEXT;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS recording_id TEXT;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
            ALTER TABLE astra_analysis ADD COLUMN IF NOT EXISTS status TEXT;
            ALTER TABLE astra_analysis ADD COLUMN IF NOT EXISTS analysis JSONB;
            ALTER TABLE astra_analysis ADD COLUMN IF NOT EXISTS short_report JSONB;
            ALTER TABLE astra_analysis ADD COLUMN IF NOT EXISTS cost_units NUMERIC;
            ALTER TABLE astra_analysis ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;""")
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,status,recording_id,client_number) VALUES(1,'101','new','rec','79990001122') RETURNING id")
        task = {'id': 1, 'client_id': 1, 'input': json.dumps({'call_id': call_id})}
        review = AsyncMock(return_value=(engine.validate(copy.deepcopy(LOST), TRANSCRIPT), 7.0))
        with patch.dict(os.environ, {'CALL_REVIEW_MODE': 'v2'}), patch.object(handler, '_bot', self.bot), \
                patch.object(handler, 'decrypt', lambda value: 'x'), \
                patch.object(handler.mango_client, 'fetch_recording', AsyncMock(return_value=b'audio')), \
                patch.object(handler, 'transcribe_bytes', AsyncMock(return_value=(TRANSCRIPT, 60))), \
                patch.object(handler.review_engine, 'run', review), \
                patch.object(handler, 'score_call', AsyncMock()) as old_score:
            result = await handler.analyze_call(self.pool, task)
        old_score.assert_not_awaited()
        self.assertEqual(review.await_args.args[1]['manager'], 'Первый')
        self.assertEqual(result['review_sent'], {'manager': 123})
        saved = await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', call_id)
        self.assertEqual((saved['status'], saved['level'], saved['score']), ('analyzed', '❌', None))
        self.assertEqual(await self.pool.fetchval('SELECT category FROM call_reviews WHERE call_id=$1', call_id), 'no_callback_date')
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM tasks WHERE type='publish_call_report'"), 0)


if __name__ == '__main__':
    unittest.main()
