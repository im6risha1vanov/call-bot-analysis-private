"""Explicit reanalysis with fake paid stages and synthetic call data only."""
import asyncio
import copy
import html
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from tests import test_unified as unified_tests
from tests.test_unified import sample
from tests.test_combined import combined_response
from tests.test_comparison import legacy_score
from unified import reanalysis, render, store
from tools import Actor, Forbidden


class CombinedStyleTests(unittest.TestCase):
    def test_verdict_is_existing_level_not_score_threshold(self):
        row = sample()
        for level, score in (('✅', 4), ('⚠️', 99), ('❌', 87)):
            row['primary_analysis'].update(level=level, score=score)
            text = html.unescape(render.summary(row))
            self.assertIn(render.verdict(row), text)
            self.assertTrue(render.verdict(row).startswith(level))
            self.assertIn(f'{score}/100', text)
        row['primary_analysis']['analysis']['call_cut_short'] = True
        self.assertTrue(render.verdict(row).startswith('❔'))
        self.assertNotIn('/100', render.summary(row))

    def test_unverified_leader_diagnosis_and_reaction_not_published(self):
        row = sample()
        leader = row['auxiliary']['combined']['readable_report']['leader']
        leader.update(cause='manager', reason='Менеджер не умеет продавать', quotes=['нет такой реплики'])
        row['auxiliary']['combined']['readable_report']['moments'][0]['reaction_quote'] = 'Выдуманная реакция'
        text = render.summary(row)
        self.assertNotIn('не умеет продавать', text)
        self.assertNotIn('Выдуманная реакция', text)

    def test_rop_document_contains_exact_current_rules_and_all_weights(self):
        from scripts.build_rop_guide import NAME, PAGES
        import analysis
        with ZipFile(Path(__file__).resolve().parents[1] / 'docs' / NAME) as archive:
            self.assertIsNone(archive.testzip())
            doc = ET.fromstring(archive.read('word/document.xml'))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(t.text or '' for t in doc.findall('.//w:t', ns))
            self.assertEqual(len(doc.findall('.//w:br[@w:type="page"]', ns)), 3)
        for _, weight, title in analysis.CRITERIA:
            self.assertIn(str(weight) + ' — ' + title, text)
        for phrase in ('course_2025_v1', 'combined_2026_v1', '75%', 'все три', 'не менее двух',
                       'Расширенный обзор', 'Сравнить методики', 'утверждённых коммерческих условий'):
            self.assertIn(phrase, text)
        self.assertEqual(len(PAGES), 4)
        self.assertNotIn('/opt/', text)


@unittest.skipUnless(os.environ.get('METHODOLOGY_TEST_DATABASE_URL'), 'Requires isolated PostgreSQL')
class ExplicitReanalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await unified_tests.UnifiedPostgresTests.asyncSetUp(self)
        root = Path(__file__).resolve().parents[1]
        await self.pool.execute((root / 'migrations/006_call_report_generation.sql').read_text())

    async def asyncTearDown(self):
        await unified_tests.UnifiedPostgresTests.asyncTearDown(self)

    add_call = unified_tests.UnifiedPostgresTests.add_call

    async def fake(self, prompt, user, settings):
        return json.dumps(combined_response() if 'readable_report' in prompt else legacy_score()), 12, {}

    async def create(self, key='owner-test'):
        return await reanalysis.create(self.pool, self.actor, self.call_id, key)

    async def test_duplicate_create_and_run_two_paid_stages_one_new_snapshot(self):
        before = await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', self.call_id)
        ids = await asyncio.gather(*[self.create() for _ in range(4)])
        self.assertEqual(len(set(ids)), 1)
        with patch.object(reanalysis.transport, 'request', AsyncMock(side_effect=self.fake)) as paid:
            result = await reanalysis.run(self.pool, self.actor, ids[0])
            await reanalysis.run(self.pool, self.actor, ids[0])
            self.assertEqual(paid.await_count, 2)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(before, await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', self.call_id))
        row = await store.access(self.pool, self.actor, ids[0])
        self.assertIn('7999', row['metadata']['phone'].replace(' ', ''))
        self.assertIn('📊 Оценка звонка', row['summary'])
        self.assertIn('combined', row['auxiliary'])
        await store.deliver(self.pool, self.bot, row, only_to=self.actor.telegram_user_id)
        await store.deliver(self.pool, self.bot, row, only_to=self.actor.telegram_user_id)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.args[0], self.actor.telegram_user_id)
        self.assertEqual(before, await self.pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', self.call_id))
        self.assertEqual(float(await self.pool.fetchval('SELECT sum(cost_units) FROM call_report_generation')), 24)
        self.assertEqual(float(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend')), 12)
        self.assertEqual(float(await self.pool.fetchval('SELECT sum(spent_units) FROM automatic_call_spend')), 12)

    async def test_restart_with_unknown_response_does_not_repeat_payment(self):
        rid = await self.create()
        with patch.object(reanalysis.transport, 'request', AsyncMock(side_effect=reanalysis.transport.RequestFailure())) as paid:
            self.assertEqual((await reanalysis.run(self.pool, self.actor, rid))['status'], 'uncertain')
            self.assertEqual((await reanalysis.run(self.pool, self.actor, rid))['status'], 'needs_review')
        paid.assert_awaited_once()
        await self.pool.execute("UPDATE call_report_generation SET status='processing' WHERE stage='score'")
        with patch.object(reanalysis.transport, 'request', AsyncMock()) as paid:
            await reanalysis.run(self.pool, self.actor, rid)
        paid.assert_not_awaited()

    async def test_received_invalid_json_not_paid_twice_and_bad_actor_not_billed(self):
        rid = await self.create()
        with patch.object(reanalysis.transport, 'request', AsyncMock(return_value=('{broken', 13, {}))) as paid:
            self.assertEqual((await reanalysis.run(self.pool, self.actor, rid))['status'], 'failed')
            await reanalysis.run(self.pool, self.actor, rid)
            paid.assert_awaited_once()
        self.assertEqual(float(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend')), 13)
        for actor in (Actor(123, 1, 'manager', '101'), self.other):
            with self.assertRaises(Forbidden):
                await reanalysis.create(self.pool, actor, self.call_id, 'bad-actor')
        with patch.object(reanalysis.transport, 'request', AsyncMock()) as paid, self.assertRaises(Forbidden):
            await reanalysis.run(self.pool, self.other, rid)
        paid.assert_not_awaited()

    async def test_budget_and_client_processing_checked_before_payment(self):
        rid = await self.create()
        await self.pool.execute('UPDATE clients SET processing_enabled=false WHERE id=1')
        with patch.object(reanalysis.transport, 'request', AsyncMock()) as paid:
            self.assertEqual((await reanalysis.run(self.pool, self.actor, rid))['status'], 'blocked')
        paid.assert_not_awaited()
        await self.pool.execute('UPDATE clients SET processing_enabled=true WHERE id=1')
        with patch.object(reanalysis, 'over_budget', AsyncMock(return_value=True)), patch.object(reanalysis.transport, 'request', AsyncMock()) as paid:
            self.assertEqual((await reanalysis.run(self.pool, self.actor, rid))['status'], 'blocked')
        paid.assert_not_awaited()

    async def test_generating_run_cannot_be_frozen_as_old_result_and_cached_overview_pinned(self):
        rid = await self.create()
        with self.assertRaises(ValueError):
            await store.prepare(self.pool, rid, wait=False)
        with patch.object(reanalysis.transport, 'request', AsyncMock(side_effect=self.fake)):
            await reanalysis.run(self.pool, self.actor, rid)
        with patch.object(reanalysis.transport, 'request', AsyncMock()) as paid:
            overview = await store.overview(self.pool, self.actor, rid)
            self.assertEqual(overview, await store.overview(self.pool, self.actor, rid))
        paid.assert_not_awaited()
        self.assertIn('Основная шкала', overview)
        self.assertIn('Дополнительная методика', overview)
