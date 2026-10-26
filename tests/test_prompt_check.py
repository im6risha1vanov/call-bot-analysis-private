"""Проверка нового промта разбора на последних звонках перед «Сохранить для всех».
Данные вымышленные."""
import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')

from review import engine, prompt_check, prompts, store
from tests import test_prompt_mode as pm
from tests.test_review import CALLBACK, LOST, TRANSCRIPT, WON


class PureTests(unittest.TestCase):
    def test_summary_label_and_changes(self):
        won = prompt_check.summary(engine.validate(copy.deepcopy(WON), CALLBACK))
        lost = prompt_check.summary(engine.validate(copy.deepcopy(LOST), TRANSCRIPT))
        self.assertTrue(won['success'])
        self.assertEqual(lost['category'], LOST['key_moment']['category'])
        self.assertIn('✅', prompt_check.label(won))
        self.assertIn('без результата', prompt_check.label(lost))
        self.assertEqual(prompt_check.changed(won, won), [])
        self.assertIn('результат', prompt_check.changed(won, lost))
        other = {**lost, 'category': 'lost_dmp'}
        self.assertEqual(prompt_check.changed(lost, other), ['категория'])
        self.assertEqual(prompt_check.changed(lost, prompt_check.summary(None)), ['сбой'])
        self.assertEqual(prompt_check.label(prompt_check.summary(None)), 'сбой')

    def test_verdict(self):
        base = {'total': 20, 'same': 20, 'diff': 0, 'broken': 0, 'success_before': 10, 'success_after': 10}
        self.assertIn('ничего не сломала', prompt_check.verdict(base))
        self.assertIn('нельзя', prompt_check.verdict({**base, 'broken': 1}))
        self.assertIn('меньше', prompt_check.verdict({**base, 'success_after': 4, 'diff': 6}))
        self.assertIn('в 6 звонках', prompt_check.verdict({**base, 'diff': 6, 'same': 14}))
        self.assertIn('не на чем', prompt_check.verdict({**base, 'total': 0}))

    def test_objection_block_is_in_code_prompt_and_can_be_added_to_saved_text(self):
        block = prompts.code_block()
        self.assertTrue(block.startswith(prompts.BLOCK_MARK))
        self.assertIn('Пришлите на почту', block)
        self.assertIn(block, engine.PROMPT)
        self.assertNotIn('## Формат ответа', block)
        saved = 'Свой текст.\n\n' + prompts.BLOCK_BEFORE + ' в том же формате.'
        merged = prompts.with_block(saved)
        self.assertLess(merged.index(prompts.BLOCK_MARK), merged.index(prompts.BLOCK_BEFORE))
        self.assertIsNone(prompts.with_block(merged))
        self.assertTrue(prompts.with_block('Короткий текст.').endswith(block))


@unittest.skipUnless(pm.DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class CheckTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = pm.StoreTests.asyncSetUp
    asyncTearDown = pm.StoreTests.asyncTearDown

    async def add_call(self, data, transcript, minutes_ago):
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,'79990001122') RETURNING id",
            transcript, datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
        call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
        async with self.pool.acquire() as conn:
            await store.save(conn, call, engine.validate(copy.deepcopy(data), transcript), 1.0)
        return call_id

    async def test_balanced_pick_and_old_vs_new(self):
        await self.pool.execute('TRUNCATE calls CASCADE')
        await self.pool.execute('DELETE FROM astra_daily_spend')
        won = [await self.add_call(WON, CALLBACK, m) for m in (1, 2, 3)]
        lost = [await self.add_call(LOST, TRANSCRIPT, m) for m in (4, 5, 6)]
        self.assertEqual(await prompt_check.pick_calls(self.pool, 1, 4), won[:2] + lost[:2])
        self.assertEqual(len(await prompt_check.pick_calls(self.pool, 1, 20)), 6)

        old_text = prompts.full_review(prompts.default('review'))

        async def run(transcript, meta, prompt=None):
            if prompt == old_text:  # действующий: как сохранено
                data = WON if transcript == CALLBACK else LOST
            else:  # новый текст «теряет» результат у успешных звонков
                data = LOST
            return engine.validate(copy.deepcopy(data), transcript), 0.5

        report = await prompt_check.check(self.pool, 1, 'Новый текст.', run=run, limit=6)
        self.assertEqual((report['total'], report['same'], report['diff'], report['broken']), (6, 3, 3, 0))
        self.assertEqual((report['success_before'], report['success_after']), (3, 0))
        self.assertIsNone(report['old_version'])
        self.assertIn('меньше', prompt_check.verdict(report))
        text = prompt_check.message(report)
        self.assertIn('было: повторный · ✅', text)
        self.assertIn('стало: холодный · без результата', text)
        self.assertEqual(float(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend')), 6.0)

        broken = AsyncMock(side_effect=ValueError('Expecting value'))
        report = await prompt_check.check(self.pool, 1, 'Сломанный.', run=broken, limit=2)
        self.assertEqual(report['broken'], 2)
        self.assertIn('нельзя', prompt_check.verdict(report))
        await self.pool.execute('DELETE FROM astra_daily_spend')

    async def test_sync_review_block(self):
        self.assertIn('ничего делать не нужно', await prompts.sync_review_block(self.pool))
        old = await prompts.save(self.pool, 'review', 'Свой общий текст.', None, 900, 'all')
        out = await prompts.sync_review_block(self.pool)
        self.assertIn(f'use {old}', out)
        body, version = await prompts.common(self.pool, 'review')
        self.assertNotEqual(version, old)
        self.assertIn(prompts.BLOCK_MARK, body)
        self.assertIn('уже есть', await prompts.sync_review_block(self.pool))
        await prompts.activate(self.pool, 'review', old, 0, 'all')  # откат
        self.assertEqual(await prompts.common(self.pool, 'review'), ('Свой общий текст.', old))


@unittest.skipUnless(pm.DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class GateTests(unittest.IsolatedAsyncioTestCase):
    """Бот и CRM: разбор для всех — только после проверки этого же текста."""
    asyncSetUp = pm.UiTests.asyncSetUp
    asyncTearDown = pm.StoreTests.asyncTearDown
    cq = pm.UiTests.cq

    async def test_bot_needs_check_before_save_for_all(self):
        await self.press(self.cq('pm:k:review'))
        await prompts.update_draft(self.pool, 901, body='Новый разбор.')
        preview = AsyncMock(return_value=('пример', None, 1))
        report = {'old_version': None, 'items': [], 'cost': 0.0, **prompt_check.totals([])}
        check = AsyncMock(return_value=report)
        with patch.object(self.ui, 'preview', preview), patch.object(prompt_check, 'check', check):
            await self.press(self.cq('pm:prev'))
            await self.press(self.cq('pm:save:all'))  # проверки не было: запускает её, не сохраняет
            self.assertEqual(check.await_count, 1)
            self.assertEqual((await prompts.common(self.pool, 'review'))[1], None)
            await self.press(self.cq('pm:save:all'))  # теперь сохраняет
            self.assertEqual(check.await_count, 1)
        self.assertEqual((await prompts.common(self.pool, 'review'))[0], 'Новый разбор.')

        await prompts.update_draft(self.pool, 901, body='Ещё правка.')
        check = AsyncMock(return_value={**report, 'broken': 1, 'total': 1})
        with patch.object(self.ui, 'preview', preview), patch.object(prompt_check, 'check', check):
            await self.press(self.cq('pm:prev'))
            await self.press(self.cq('pm:save:all'))
            await self.press(self.cq('pm:save:all'))  # проверка со сбоями не открывает сохранение
        self.assertEqual(check.await_count, 2)
        self.assertEqual((await prompts.common(self.pool, 'review'))[0], 'Новый разбор.')
        await self.press(self.cq('pm:save:me'))  # для себя можно и без проверки
        self.assertEqual((await prompts.resolve(self.pool, 'review', 901))[:2], ('Ещё правка.', 'personal'))

    async def test_crm_needs_check_token_for_review_for_all(self):
        from crm import prompts as crm_prompts
        who = SimpleNamespace(role='director', actor=SimpleNamespace(telegram_user_id=901, client_id=1))
        token = crm_prompts.sign(901, 'review', 'Текст.')
        with patch.object(crm_prompts.telegram, 'send', AsyncMock()):
            with self.assertRaisesRegex(ValueError, 'после проверки'):
                await crm_prompts.save(self.pool, who, 'review', 'Текст.', None, 'all', token)
            await crm_prompts.save(self.pool, who, 'review', 'Текст.', None, 'me', token)  # для себя можно
            report = {'old_version': None, 'items': [], 'cost': 0.0, **prompt_check.totals([])}
            with patch.object(prompt_check, 'check', AsyncMock(return_value=report)):
                checked = await crm_prompts.check(self.pool, who, 'review', 'Текст.')
            self.assertTrue(checked['token'])
            with self.assertRaisesRegex(ValueError, 'после проверки'):  # подпись от другого текста
                await crm_prompts.save(self.pool, who, 'review', 'Текст.', None, 'all', token,
                                       crm_prompts.sign(901, 'check:review', 'Другой.'))
            await crm_prompts.save(self.pool, who, 'review', 'Текст.', None, 'all', token, checked['token'])
            self.assertEqual((await prompts.common(self.pool, 'review'))[0], 'Текст.')
            with self.assertRaisesRegex(ValueError, 'только у разбора'):
                await crm_prompts.check(self.pool, who, 'morning', 'x')


if __name__ == '__main__':
    unittest.main()
