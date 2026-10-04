"""Synthetic data, isolated PostgreSQL, mocked providers and Telegram only."""
import asyncio
import copy
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_automatic as automatic_tests
from tests.test_methodology import TRANSCRIPT
from tests.test_combined import grade, ValidHTML
from tools import Actor, Forbidden
from unified import render, store, ui
from comparison import transport
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message, User, Chat
from queue_runner import RetryLater


def sample():
    return {'id': 1, 'call_id': 123, 'transcript': TRANSCRIPT,
            'metadata': {'manager': 'Вымышленный менеджер', 'phone': '+79990001122', 'duration_seconds': 91,
                         'call_started_at': '2026-10-04T12:30:00+03:00'},
            'primary_analysis': {'score': 47, 'level': '❌', 'analysis': {'rows': []},
                                 'short_report': {'result': 'Согласовано коммерческое предложение'}},
            'auxiliary': {'combined': grade(), 'limitations': []}}


class RendererTests(unittest.TestCase):
    def test_one_report_score_active_phone_masked_no_keys_or_duplicates(self):
        row = sample(); text = html.unescape(render.summary(row))
        self.assertIn('47/100', text); self.assertNotIn('100/100', text)
        self.assertNotIn('79990001122', text); self.assertIn('1122', text)
        self.assertIn('№123', text); self.assertIn('04.10.2026 12:30', text); self.assertIn('1:31', text)
        self.assertEqual(text.count('Да, отправьте КП'), 1)
        self.assertNotIn('next_step', text); self.assertNotIn('readable_report', text)
        self.assertNotIn('{', text)
        self.assertIn('качество', text.lower()); self.assertIn('результат', text.lower())
        self.assertEqual(render.keyboard(1).inline_keyboard[0][0].text, 'Расширенный обзор')

    def test_missing_data_does_not_invent_quality_or_errors(self):
        row = sample(); row.update(transcript='', auxiliary={})
        row['primary_analysis'] = {'score': None, 'analysis': {'call_cut_short': True}, 'short_report': {}}
        text = render.summary(row)
        self.assertIn('Данных недостаточно', text); self.assertNotIn('/100', text)
        self.assertIn('не позволяет проверить ошибки', text)

    def test_fake_quotes_and_json_are_excluded(self):
        row = sample(); data = row['auxiliary']['combined']['readable_report']
        data['strengths'][0]['quote'] = 'Клиент пообещал миллион'
        data['moments'][0]['what'] = '{"offer":"technical"}'
        text = render.summary(row)
        self.assertNotIn('миллион', text); self.assertNotIn('technical', text)

    def test_disagreement_not_reconciled_or_scores_added(self):
        row = sample(); row['primary_analysis']['analysis']['rows'] = [
            {'key': 'concrete_commitment', 'passed': True, 'applicable': True, 'evidence': 'Да, отправьте КП'}]
        text = render.summary(row)
        self.assertIn('Методики расходятся', text); self.assertIn('47/100', text)
        self.assertNotIn('Не уточнили срок', text)

    def test_long_html_emoji_safe_one_message(self):
        row = sample(); row['metadata']['manager'] = '<script> & 👨' * 1000
        data = row['auxiliary']['combined']['readable_report']
        for name in ('next_focus', 'exercise'):
            data[name] = '<>&😀' * 1000
        text = render.summary(row)
        self.assertLessEqual(render.units(text), 4000)
        parsed = ValidHTML(); parsed.feed(text); self.assertEqual(parsed.stack, [])
        self.assertIn('&lt;script&gt;', text)

    def test_baseline_only_format_and_detailed_scales_are_separate(self):
        row = sample(); row['auxiliary'] = {}
        self.assertIn('47/100', render.summary(row))
        row = sample(); text = render.expanded(row)
        self.assertIn('Числовой балл этой методики не откалиброван', text)
        self.assertIn('Основная шкала', text); self.assertIn('Критерии', text)
        self.assertNotIn('Стоимость: $', text)

    def test_number_inside_quote_is_masked_and_quote_not_repeated(self):
        row = sample(); row['auxiliary'] = {}
        row['transcript'] += ' Клиент: Звоните +7 (999) 000-11-22.'
        row['primary_analysis']['short_report']['weak'] = [{'text': '«Звоните +7 (999) 000-11-22» — срок не уточнён'}]
        text = html.unescape(render.summary(row))
        self.assertNotIn('999) 000', text)
        self.assertEqual(text.count('[номер скрыт]'), 1)

    def test_guide_ooxml_content_and_exact_button_names(self):
        from scripts.build_manager_guide import PAGES, NAME
        import training_menu
        import xml.etree.ElementTree as ET
        from zipfile import ZipFile
        path = Path(__file__).resolve().parents[1] / 'docs' / NAME
        with ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            doc = ET.fromstring(archive.read('word/document.xml'))
            namespace = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(e.text or '' for e in doc.findall('.//w:t', namespace))
            self.assertEqual(len(doc.findall('.//w:br[@w:type="page"]', namespace)), 2)
        for title in training_menu.ACTIONS + ('Расширенный обзор', 'К краткому разбору', 'Закрыть обзор'):
            self.assertIn(title, text)
        for secret in ('/opt/', 'CVC_', 'cost_units', '/approvals', '/assign_train', 'DATABASE_URL'):
            self.assertNotIn(secret, text)
        self.assertIn('Вымышленный пример', text); self.assertEqual(len(PAGES), 3)

    def test_uncertain_delivery_not_reported_as_quiet(self):
        import oversight
        metrics = {'failures': [], 'stuck': [], 'limit_hits': [], 'spend': {'rop_agent_usd_2d': 0},
                   'queue': {'new': 0, 'processing': 0, 'awaiting_approval': 0},
                   'report_delivery': {'uncertain': 1}}
        self.assertFalse(oversight.is_quiet(metrics, 3))
        metrics['report_delivery'] = {}; self.assertTrue(oversight.is_quiet(metrics, 3))
        metrics['queue']['new'] = 1; self.assertFalse(oversight.is_quiet(metrics, 3))


@unittest.skipUnless(os.environ.get('METHODOLOGY_TEST_DATABASE_URL'), 'Requires isolated PostgreSQL')
class UnifiedPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await automatic_tests.AutomaticPostgresTests.asyncSetUp(self)
        await self.pool.execute("UPDATE astra_analysis SET short_report=$2::jsonb,level='❌' WHERE call_id=$1", self.call_id,
                                json.dumps({'result': 'Согласовано КП', 'good': [], 'weak': []}))
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),
                                   send_document=AsyncMock(return_value=SimpleNamespace(message_id=43)))
    async def asyncTearDown(self):
        await automatic_tests.AutomaticPostgresTests.asyncTearDown(self)
    add_call = automatic_tests.AutomaticPostgresTests.add_call

    async def capture(self, key='task:1', deliver=False):
        return await store.capture(self.pool, self.call_id, 1, key, deliver=deliver)

    async def ready(self, key='task:1'):
        return await store.prepare(self.pool, await self.capture(key), wait=False)

    async def test_same_task_concurrent_capture_one_run_and_one_publication_task(self):
        ids = await asyncio.gather(*[self.capture(deliver=True) for _ in range(6)])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM tasks WHERE type='publish_call_report'"), 1)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM call_report_runs'), 1)

    async def test_delivery_once_each_recipient_retry_and_restart(self):
        row = await self.ready()
        await asyncio.gather(store.deliver(self.pool, self.bot, row), store.deliver(self.pool, self.bot, row))
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertEqual({c.args[0] for c in self.bot.send_message.await_args_list}, {123, 900, 901})
        for c in self.bot.send_message.await_args_list:
            self.assertEqual(len(c.kwargs['reply_markup'].inline_keyboard), 1)
        from handlers.publish_call_report import recover_report_delivery
        await recover_report_delivery(self.pool)
        await store.deliver(self.pool, self.bot, await store.prepare(self.pool, row['id']))
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM call_report_delivery WHERE status='sent'"), 3)

    async def test_unknown_delivery_no_blind_resend_after_restart(self):
        row = await self.ready()
        self.bot.send_message.side_effect = TimeoutError('synthetic')
        await store.deliver(self.pool, self.bot, row)
        from handlers.publish_call_report import recover_report_delivery
        await self.pool.execute("UPDATE call_report_delivery SET status='sending' WHERE recipient=123")
        await recover_report_delivery(self.pool)
        self.bot.send_message.side_effect = None
        await store.deliver(self.pool, self.bot, row)
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM call_report_delivery WHERE status='uncertain'"), 3)

    async def test_known_rejection_retries_only_missing_recipient(self):
        row = await self.ready()
        async def send(recipient, *args, **kwargs):
            if recipient == 900:
                raise TelegramBadRequest(method=None, message='synthetic rejected')
            return SimpleNamespace(message_id=42)
        self.bot.send_message.side_effect = send
        with self.assertRaises(RetryLater):
            await store.deliver(self.pool, self.bot, row)
        self.bot.send_message.side_effect = None
        await store.deliver(self.pool, self.bot, row)
        self.assertEqual(self.bot.send_message.await_count, 4)
        self.assertEqual(self.bot.send_message.await_args.args[0], 900)

    async def test_explicit_new_run_distinct_receipts_same_call(self):
        one = await self.ready(); two = await self.ready('explicit:new:2')
        self.assertNotEqual(one['id'], two['id'])
        await store.deliver(self.pool, self.bot, one); await store.deliver(self.pool, self.bot, two)
        self.assertEqual(self.bot.send_message.await_count, 6)

    async def test_manager_routes_preserved_success_to_leadership_only(self):
        await self.pool.execute("UPDATE astra_analysis SET level='✅' WHERE call_id=$1", self.call_id)
        row = await self.ready(); await store.deliver(self.pool, self.bot, row)
        self.assertEqual({c.args[0] for c in self.bot.send_message.await_args_list}, {900, 901})

    async def test_immutable_snapshot_across_mode_switch_reanalysis_and_restart(self):
        row = await self.ready(); before = row['summary']
        await self.pool.execute("UPDATE calls SET transcript='new transcript' WHERE id=$1", self.call_id)
        await self.pool.execute('UPDATE astra_analysis SET score=99 WHERE call_id=$1', self.call_id)
        with patch.object(store.runtime, 'settings', return_value={'mode': 'legacy'}):
            after = await store.access(self.pool, self.actor, row['id'])
            detail = await store.overview(self.pool, self.actor, row['id'])
        self.assertEqual(after['summary'], before); self.assertEqual(after['transcript'], TRANSCRIPT)
        self.assertEqual(after['primary_analysis']['score'], 87)
        self.assertIn('87/100', detail); self.assertNotIn('99/100', detail)

    async def test_cached_overview_double_click_no_llm(self):
        row = await self.ready()
        with patch.object(render, 'expanded', wraps=render.expanded) as generator, patch.object(transport, 'request', AsyncMock()) as paid:
            texts = await asyncio.gather(*[store.overview(self.pool, self.actor, row['id']) for _ in range(5)])
            self.assertEqual(generator.call_count, 1); self.assertEqual(len(set(texts)), 1)
            await store.overview(self.pool, self.actor, row['id'])
            self.assertEqual(generator.call_count, 1); paid.assert_not_awaited()

    async def test_overview_generation_error_keeps_summary_and_retry_is_safe(self):
        row = await self.ready()
        with patch.object(render, 'expanded', side_effect=ValueError('synthetic')):
            with self.assertRaises(ValueError): await store.overview(self.pool, self.actor, row['id'])
        saved = await store.access(self.pool, self.actor, row['id'])
        self.assertEqual(saved['summary'], row['summary']); self.assertIsNone(saved['expanded'])
        self.assertTrue(await store.overview(self.pool, self.actor, row['id']))

    async def test_actor_manager_owner_head_and_tenant_scope(self):
        row = await self.ready()
        for actor in (None, self.other, Actor(124, 1, 'manager', '102')):
            with self.assertRaises(Forbidden): await store.overview(self.pool, actor, row['id'])
        for actor in (self.actor, Actor(123, 1, 'manager', '101'), Actor(901, 1, 'head', 'head')):
            self.assertTrue(await store.overview(self.pool, actor, row['id']))

    async def test_wait_uses_saved_auxiliary_and_timeout_delivers_only_once(self):
        rid = await self.capture()
        with self.assertRaises(RetryLater): await store.prepare(self.pool, rid)
        await self.pool.execute("UPDATE call_report_runs SET created_at=now()-interval '31 minutes' WHERE id=$1", rid)
        row = await store.prepare(self.pool, rid)
        self.assertIn('ещё не готова', row['summary'])
        await store.deliver(self.pool, self.bot, row)
        # Later completion never causes another ordinary report for that run.
        self.assertEqual((await store.prepare(self.pool, rid))['summary'], row['summary'])
        await store.deliver(self.pool, self.bot, row); self.assertEqual(self.bot.send_message.await_count, 3)

    async def test_saved_historical_view_no_tasks_no_paid_request_no_original_mutation(self):
        before = await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', self.call_id)
        with patch.object(transport, 'request', AsyncMock()) as paid:
            rid = await store.saved_view(self.pool, self.actor, self.call_id)
            self.assertEqual(rid, await store.saved_view(self.pool, self.actor, self.call_id))
        paid.assert_not_awaited()
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM tasks'), 0)
        self.assertEqual(before, await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', self.call_id))

    async def test_complete_real_pipeline_keeps_two_internal_methods_one_delivery(self):
        import handlers.analyze_call as original
        from handlers.evaluate_course import evaluate_course
        from handlers.publish_call_report import publish_call_report
        from automatic import jobs as automatic_jobs
        from methodology import transport as course_transport
        from tests.test_methodology import response
        from tests.test_combined import combined_response
        before = await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', self.call_id)
        task = {'id': 777, 'client_id': 1, 'input': json.dumps({'call_id': self.call_id})}
        with patch.object(original, '_bot', self.bot), patch.object(original, 'score_call', AsyncMock()) as primary_paid:
            await original.analyze_call(self.pool, task)
            primary_paid.assert_not_awaited(); self.bot.send_message.assert_not_awaited()
            publishing = await self.pool.fetchrow("SELECT * FROM tasks WHERE type='publish_call_report'")
            with self.assertRaises(RetryLater): await publish_call_report(self.pool, publishing)
            with patch.object(course_transport, 'request', AsyncMock(return_value=(json.dumps(response()), 5))):
                await evaluate_course(self.pool, await self.pool.fetchrow("SELECT * FROM tasks WHERE type='evaluate_course'"))
            with patch.object(transport, 'request', AsyncMock(return_value=(json.dumps(combined_response()), 12, {}))):
                await automatic_jobs.run(self.pool, await self.pool.fetchrow("SELECT * FROM tasks WHERE type='automatic_combined_call'"))
            result = await publish_call_report(self.pool, publishing)
            await publish_call_report(self.pool, publishing)
        self.assertEqual(result['delivery'], {'sent': 3}); self.assertEqual(self.bot.send_message.await_count, 3)
        row = store.unpack(await self.pool.fetchrow('SELECT * FROM call_report_runs'))
        self.assertIn('course', row['auxiliary']); self.assertIn('combined', row['auxiliary'])
        self.assertIn('87/100', row['summary'])
        self.assertEqual(row['primary_analysis']['analysis'], json.loads(before['analysis']))
        self.assertIsNone(row['auxiliary']['combined']['quality']['score'])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM analysis_comparisons'), 0)
        # A later change of a secondary evaluation cannot mutate the delivered run.
        await self.pool.execute("UPDATE automatic_call_reports SET result='{}'")
        self.assertEqual((await store.prepare(self.pool, row['id']))['summary'], row['summary'])

    async def test_digest_keeps_statistics_without_second_call_fragment(self):
        import astra_worker
        now = datetime.now(timezone.utc)
        await self.pool.execute('UPDATE astra_analysis SET short_report=$2::jsonb WHERE call_id=$1', self.call_id,
                                json.dumps({'weak': [{'text': 'Отдельный старый фрагмент'}]}))
        # Rows without a new run retain historical behavior.
        text = await astra_worker.build_manager_digest(self.pool, {'id': 1}, '101', 'Первый', now.replace(year=2020), now.replace(year=2030))
        self.assertIn('Отдельный старый фрагмент', text)
        await self.capture()
        text = await astra_worker.build_manager_digest(self.pool, {'id': 1}, '101', 'Первый', now.replace(year=2020), now.replace(year=2030))
        self.assertIn('Звонков:', text); self.assertNotIn('Отдельный старый фрагмент', text)

    async def test_legacy_switch_keeps_unified_format_without_auxiliary_payment(self):
        with patch.object(store.runtime, 'shadow_enabled', return_value=False), patch.object(store.runtime, 'settings', return_value={'mode': 'legacy'}):
            rid = await self.capture('legacy:2')
        with patch.object(transport, 'request', AsyncMock()) as paid:
            row = await store.prepare(self.pool, rid)
            await store.overview(self.pool, self.actor, rid)
        self.assertEqual(row['configuration']['mode'], 'legacy'); paid.assert_not_awaited()
        self.assertIn('87/100', row['summary']); self.assertNotIn('course', row['auxiliary'])

    async def test_release_migrates_all_additive_tables_atomically_and_idempotently(self):
        from scripts import release_manager
        root = Path(__file__).resolve().parents[1]
        files = sorted(str(p.relative_to(root)) for p in (root / 'migrations').glob('*.sql'))
        def saved_file(args, **kwargs): return (root / args[2].split(':', 1)[1]).read_bytes()
        before = await self.pool.fetch('SELECT * FROM astra_analysis ORDER BY call_id')
        with patch.object(release_manager, 'PROJECT', root), patch('dotenv.dotenv_values', return_value={'DATABASE_URL': os.environ['METHODOLOGY_TEST_DATABASE_URL']}), patch.object(release_manager, 'command', return_value='\n'.join(files)), patch.object(release_manager.subprocess, 'check_output', side_effect=saved_file):
            await release_manager.database('migrate', release='0' * 40)
            await release_manager.database('migrate', release='0' * 40)
        self.assertEqual(before, await self.pool.fetch('SELECT * FROM astra_analysis ORDER BY call_id'))
        self.assertTrue(await store.installed(self.pool))

    async def test_callbacks_use_click_user_ack_first_and_no_payment(self):
        row = await self.ready(); router = Router(); ui.install(router, lambda: self.pool)
        telegram = Bot('123456:synthetic_test_token')
        async def click(user, data, *, chat=None):
            message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=chat or user, type='private'),
                              from_user=User(id=777, is_bot=True, first_name='Bot'), text='Report')
            cq = CallbackQuery(id='fake', from_user=User(id=user, is_bot=False, first_name='Person'), chat_instance='fake', data=data, message=message).as_(telegram)
            order = []
            async def ack(*args, **kwargs): order.append('ack')
            async def send(*args, **kwargs): order.append('send')
            with patch.object(CallbackQuery, 'answer', AsyncMock(side_effect=ack)) as answered, patch.object(Bot, 'send_message', AsyncMock(side_effect=send)) as sent, patch.object(transport, 'request', AsyncMock()) as paid:
                await router.propagate_event('callback_query', cq)
                answered.assert_awaited_once(); paid.assert_not_awaited(); self.assertEqual(order[0], 'ack')
                return sent
        for user in (123, 900, 901):
            self.assertIn('Расширенный обзор', (await click(user, f'uv:e:{row["id"]}')).await_args.args[1])
        for user, data, chat in ((124, f'uv:e:{row["id"]}', None), (902, f'uv:e:{row["id"]}', None),
                                 (900, f'uv:e:{row["id"]}', 901), (900, 'uv:bad:1', None), (900, 'uv:e:999999', None)):
            self.assertNotIn('Основная шкала', (await click(user, data, chat=chat)).await_args.args[1])
        self.assertIn('87/100', (await click(900, f'uv:s:{row["id"]}')).await_args.args[1])
        await telegram.session.close()

    async def test_long_overview_is_file_and_return_is_pinned(self):
        row = await self.ready()
        await self.pool.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', row['id'], 'Длинный обзор\n' * 2000)
        await ui.show(self.bot, self.pool, self.actor, row['id'], detailed=True)
        self.bot.send_document.assert_awaited_once(); self.bot.send_message.assert_not_awaited()
        markup = self.bot.send_document.await_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, f'uv:s:{row["id"]}')
