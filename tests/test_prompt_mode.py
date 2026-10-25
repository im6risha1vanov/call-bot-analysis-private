"""Режим промта: версии, личные и общие промты, откат и защита от сломанного промта.
Данные вымышленные."""
import copy
import json
import os
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')

from review import engine, prompts, store
from tools import Actor
from tests.test_review import CALLBACK, LOST, TRANSCRIPT, WON

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
EDITORS = {'PROMPT_EDITORS': '900, 901'}


class PureTests(unittest.TestCase):
    def test_review_head_plus_locked_format_is_the_original_prompt(self):
        self.assertEqual(prompts.full_review(prompts.default('review')), engine.PROMPT)
        self.assertNotIn('## Формат ответа', prompts.default('review'))
        edited = prompts.full_review('Новый короткий промт.')
        self.assertTrue(edited.endswith(prompts.REVIEW_TAIL))
        self.assertIn('"manager_text"', edited)

    def test_digest_placeholders_and_missing_period(self):
        values = prompts.digest_values('weekly', 'Europe/Moscow', today=date(2026, 10, 15))
        self.assertEqual(values, {'start': '2026-10-05', 'end': '2026-10-12',
                                  'prev_start': '2026-09-28', 'prev_end': '2026-10-05'})
        text = prompts.render_digest(prompts.default('weekly'), values)
        self.assertIn('(2026-10-05–2026-10-12)', text)
        self.assertNotIn('{', text)
        bare = prompts.render_digest('Только цифры по менеджерам.', values)
        self.assertTrue(bare.endswith('Период: 2026-10-05–2026-10-12; для сравнения 2026-09-28–2026-10-05.'))
        self.assertEqual(prompts.digest_values('monthly', 'Europe/Moscow', today=date(2026, 1, 1)),
                         {'start': '2025-12-01', 'end': '2026-01-01'})

    def test_editors_from_env(self):
        with patch.dict(os.environ, {'PROMPT_EDITORS': '841868875,304327068'}):
            self.assertTrue(prompts.is_editor(304327068))
            self.assertFalse(prompts.is_editor(123))
        with patch.dict(os.environ, {'PROMPT_EDITORS': ''}):
            self.assertEqual(prompts.editors(), [])

    def test_diff_shows_added_and_removed_lines_escaped(self):
        text = prompts.diff('один\nдва <b>\nтри', 'один\nтри\nчетыре')
        self.assertIn('➖ два &lt;b&gt;', text)
        self.assertIn('➕ четыре', text)

    def test_broken_review(self):
        good = engine.validate(copy.deepcopy(LOST), TRANSCRIPT)
        self.assertIsNone(prompts.broken_review(good, good))
        self.assertIn('не по формату', prompts.broken_review({'text': 'привет'}))
        lost = engine.validate({'kind': 'no_conversation'}, '')
        self.assertIn('не разобран', prompts.broken_review(lost, good))


class EditTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_returns_new_text_or_none(self):
        call = AsyncMock(return_value=({'reply': 'Убрал.', 'prompt': 'Новый текст', 'changes': 'убран раздел'}, 3.0))
        result, cost = await prompts.edit('morning', 'Старый текст', [], 'убери раздел', call=call)
        self.assertEqual((result['prompt'], result['changes'], cost), ('Новый текст', 'убран раздел', 3.0))
        system, user, _ = call.await_args.args
        self.assertIn('{start}', system)
        self.assertIn('Старый текст', user)
        same = AsyncMock(return_value=({'reply': 'Это уже так.', 'prompt': 'Старый текст'}, 1.0))
        self.assertIsNone((await prompts.edit('morning', 'Старый текст', [], '?', call=same))[0]['prompt'])
        huge = AsyncMock(return_value=({'reply': 'ok', 'prompt': 'x' * (prompts.MAX_BODY + 1)}, 1.0))
        self.assertIsNone((await prompts.edit('review', 'a', [], 'b', call=huge))[0]['prompt'])


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS duration_seconds INTEGER DEFAULT 90; ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT; ALTER TABLE employees ADD COLUMN IF NOT EXISTS id BIGSERIAL")
        for name in ('008_call_reviews.sql', '009_callback_tasks.sql', '011_review_copies.sql', '012_prompt_versions.sql', '031_company_prompts.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients, prompt_active, prompt_versions, prompt_drafts CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'101',123,'manager','Первый'),(1,'',900,'owner','Григорий'),(1,'head2',901,'head','Евгений')")
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
                                   send_document=AsyncMock())
        self.env = patch.dict(os.environ, EDITORS)
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.pool.close()

    async def test_migration_is_repeatable_and_seed_keeps_originals_once(self):
        root = Path(__file__).resolve().parents[1]
        await self.pool.execute((root / 'migrations/012_prompt_versions.sql').read_text())
        self.assertEqual(await prompts.seed(self.pool), len(prompts.KINDS))
        self.assertEqual(await prompts.seed(self.pool), 0)
        body = await self.pool.fetchval("SELECT body FROM prompt_versions WHERE kind='review'")
        self.assertEqual(prompts.full_review(body), engine.PROMPT)

    async def test_personal_common_and_rollback(self):
        self.assertEqual(await prompts.resolve(self.pool, 'morning', 901), (prompts.default('morning'), 'default', None))
        mine = await prompts.save(self.pool, 'morning', 'Только итоги {start}', 'короче', 901, 'me')
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 901))[1:], ('personal', mine))
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 900))[1], 'default')  # Григорию не изменилось

        everyone = await prompts.save(self.pool, 'morning', 'Для всех {start}', 'общая', 900, 'all')
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 900))[1:], ('common', everyone))
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 901))[1:], ('personal', mine))  # своя остаётся
        self.assertEqual(await prompts.common(self.pool, 'morning'), ('Для всех {start}', everyone))

        listed = await prompts.versions(self.pool, 'morning', 901)
        original = await prompts.original_id(self.pool, 'morning')
        self.assertEqual([v['id'] for v in listed], [everyone, mine, original])
        self.assertNotIn(mine, [v['id'] for v in await prompts.versions(self.pool, 'morning', 900)])  # чужая личная не видна

        await prompts.activate(self.pool, 'morning', original, 900, 'all')  # «как в игре»: назад к исходной
        self.assertEqual(await prompts.common(self.pool, 'morning'), (prompts.default('morning'), original))
        with self.assertRaises(ValueError):
            await prompts.activate(self.pool, 'morning', mine, 900, 'me')  # чужое сохранение
        self.assertTrue(await prompts.drop_personal(self.pool, 'morning', 901))
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 901))[1:], ('common', original))

    async def test_save_for_all_drops_own_personal(self):
        await prompts.save(self.pool, 'weekly', 'Моё {start}', None, 901, 'me')
        version = await prompts.save(self.pool, 'weekly', 'Всем {start}', None, 901, 'all')
        self.assertEqual((await prompts.resolve(self.pool, 'weekly', 901))[1:], ('common', version))
        with self.assertRaises(ValueError):
            await prompts.save(self.pool, 'weekly', '   ', None, 901, 'me')

    async def test_draft_lifecycle(self):
        row = await prompts.open_draft(self.pool, 901, 'monthly')
        self.assertEqual(row['body'], prompts.default('monthly'))
        await prompts.update_draft(self.pool, 901, body='Новый', history=[{'ask': 'a', 'reply': 'b'}], preview_ok=True)
        row = await prompts.draft(self.pool, 901)
        self.assertEqual((row['body'], row['start_body'] == prompts.default('monthly')), ('Новый', True))
        self.assertEqual(json.loads(row['history']), [{'ask': 'a', 'reply': 'b'}])
        await self.pool.execute("UPDATE prompt_drafts SET updated_at=now() - interval '4 hours'")
        self.assertIsNone(await prompts.draft(self.pool, 901))  # устарела
        await prompts.close_draft(self.pool, 901)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM prompt_drafts'), 0)

    async def test_common_review_prompt_falls_back_to_original_and_tells_editors(self):
        good = engine.validate(copy.deepcopy(LOST), TRANSCRIPT)
        run = AsyncMock(return_value=(good, 2.0))
        self.assertEqual(await prompts.run_common_review(self.pool, TRANSCRIPT, {}, self.bot, run=run), (good, 2.0))
        self.assertEqual(run.await_args.kwargs, {})  # исходный промт, как раньше
        await prompts.save(self.pool, 'review', 'Сломанный промт', 'тест', 900, 'all')
        run = AsyncMock(side_effect=[ValueError('Expecting value: line 1'), (good, 2.0)])
        data, cost = await prompts.run_common_review(self.pool, TRANSCRIPT, {}, self.bot, run=run)
        self.assertEqual(data, good)
        self.assertEqual(run.await_args_list[0].kwargs, {'prompt': prompts.full_review('Сломанный промт')})
        self.assertEqual(run.await_args_list[1].kwargs, {})
        self.assertEqual(sorted(c.args[0] for c in self.bot.send_message.await_args_list), [900, 901])
        self.assertIn('не в формате JSON', self.bot.send_message.await_args.args[1])

    async def add_call(self, data, transcript):
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,'79990001122') RETURNING id",
            transcript, datetime.fromisoformat('2026-10-07T11:40:00+00:00'))
        call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
        async with self.pool.acquire() as conn:
            await store.save(conn, call, engine.validate(copy.deepcopy(data), transcript), 12.5)
        return call_id

    async def test_personal_review_copy_and_fallback(self):
        await prompts.save(self.pool, 'review', 'Пиши подробнее.', None, 901, 'me')
        mine = engine.validate({**copy.deepcopy(WON), 'details': 'ЛИЧНЫЙ РАЗБОР'}, CALLBACK)
        with patch.dict(os.environ, {'REVIEW_COPY_TO': '900,901'}), \
                patch.object(engine, 'run', AsyncMock(return_value=(mine, 4.0))) as run:
            call_id = await self.add_call(WON, CALLBACK)
            self.assertEqual(await store.deliver_now(self.pool, self.bot, call_id), {'copy': [900, 901]})
        self.assertEqual(run.await_count, 1)  # только для того, у кого личный промт
        self.assertEqual(run.await_args.kwargs, {'prompt': prompts.full_review('Пиши подробнее.')})
        self.assertIn('Первый', run.await_args.args[1]['manager'])
        texts = {c.args[0]: c.args[1] for c in self.bot.send_message.await_args_list}
        self.assertIn('ЛИЧНЫЙ РАЗБОР', texts[901])
        self.assertNotIn('ЛИЧНЫЙ РАЗБОР', texts[900])
        self.assertEqual(float(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend')), 4.0)

        self.bot.send_message.reset_mock()
        with patch.dict(os.environ, {'REVIEW_COPY_TO': '901'}), \
                patch.object(engine, 'run', AsyncMock(side_effect=RuntimeError('Astra 500: сбой'))):
            call_id = await self.add_call(WON, CALLBACK)
            self.assertEqual(await store.deliver_now(self.pool, self.bot, call_id), {'copy': [901]})
        notice, standard = [c.args[1] for c in self.bot.send_message.await_args_list]
        self.assertIn('не сработал', notice)
        self.assertIn('Astra 500', notice)
        self.assertIn('Клиент согласился на созвон', standard)

    async def test_digests_one_answer_per_text_and_fallback(self):
        from handlers import rop_digest
        actors = [Actor(900, 1, 'head', None), Actor(901, 1, 'head', None)]
        asked = []

        async def answer(pool, actor, question):
            asked.append(question)
            if question.startswith('СЛОМАН'):
                raise RuntimeError('нет баланса')
            return f'ответ на: {question[:12]}'

        broadcast = AsyncMock(side_effect=lambda group, header, text: len(group))
        with patch.object(rop_digest, '_bot', self.bot):
            done = await rop_digest._digest(self.pool, 1, 'morning', 'H', actors, answer=answer, broadcast=broadcast)
            self.assertEqual((done['sent_to'], done['versions'], len(asked)), (2, 1, 1))  # как раньше: один запрос

            await prompts.save(self.pool, 'morning', 'Коротко за {start}', None, 901, 'me')
            asked.clear()
            await rop_digest._digest(self.pool, 1, 'morning', 'H', actors, answer=answer, broadcast=broadcast)
            self.assertEqual(len(asked), 2)
            self.assertTrue(asked[1].startswith('Коротко за 20'))

            await prompts.save(self.pool, 'morning', 'СЛОМАН {start}', None, 901, 'me')
            broadcast.reset_mock()
            await rop_digest._digest(self.pool, 1, 'morning', 'H', actors, answer=answer, broadcast=broadcast)
        sent = {call.args[0][0].telegram_user_id: call.args[2] for call in broadcast.await_args_list}
        self.assertEqual(sent[901], sent[900])  # стандартный отчёт вместо сломанного
        self.assertIn('нет баланса', self.bot.send_message.await_args.args[1])
        self.assertEqual(self.bot.send_message.await_args.args[0], 901)


class FakeRouter:
    def __init__(self):
        self.messages, self.callbacks = [], []

    def message(self, *filters):
        def wrap(func):
            self.messages.append((filters, func))
            return func
        return wrap

    def callback_query(self, *filters):
        def wrap(func):
            self.callbacks.append((filters, func))
            return func
        return wrap


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class UiTests(unittest.IsolatedAsyncioTestCase):
    """Кнопки режима промта на той же тестовой базе."""
    asyncTearDown = StoreTests.asyncTearDown

    async def asyncSetUp(self):
        await StoreTests.asyncSetUp(self)
        from review import prompt_ui
        self.ui = prompt_ui
        self.router = FakeRouter()
        prompt_ui.install(self.router, lambda: self.pool)
        self.press = self.router.callbacks[0][1]
        self.ask = next(func for filters, func in self.router.messages if func.__name__ == 'ask')
        self.in_session = self.router.messages[-1][0][1]

    def cq(self, data, tg=901):
        chat = SimpleNamespace(type='private', id=tg)
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=tg), answer=AsyncMock(), bot=self.bot,
                               message=SimpleNamespace(chat=chat))

    def msg(self, text, tg=901):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(id=tg), chat=SimpleNamespace(type='private', id=tg),
                               bot=self.bot, answer=AsyncMock(return_value=SimpleNamespace(
                                   delete=AsyncMock(), edit_text=AsyncMock())))

    async def test_edit_preview_save_flow(self):
        self.assertFalse(await self.in_session(self.msg('привет')))
        await self.press(self.cq('pm:k:morning'))
        self.assertTrue(await self.in_session(self.msg('убери лишнее')))
        self.assertFalse(await self.in_session(self.msg('/help')))
        self.assertFalse(await self.in_session(self.msg('убери лишнее', tg=123)))  # не редактор

        edited = ({'reply': 'Сделал короче.', 'prompt': 'Коротко за {start}', 'changes': 'короче'}, 1.0)
        with patch.object(prompts, 'edit', AsyncMock(return_value=edited)):
            await self.ask(self.msg('пиши короче'))
        self.assertEqual((await prompts.draft(self.pool, 901))['body'], 'Коротко за {start}')

        preview = AsyncMock(return_value=('<b>пример</b>', None, None))
        with patch.object(self.ui, 'preview', preview):
            await self.press(self.cq('pm:save:me'))  # без примера не сохраняет, а показывает пример
            self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM prompt_active'), 0)
            self.assertEqual(preview.await_count, 1)
            await self.press(self.cq('pm:save:all'))
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 900))[0], 'Коротко за {start}')
        told = [c for c in self.bot.send_message.await_args_list if c.args[0] == 900]
        self.assertIn('Евгений изменил промт', told[-1].args[1])

        version = await prompts.original_id(self.pool, 'morning')
        await self.press(self.cq(f'pm:use:{version}:all'))  # откат к исходной
        self.assertEqual((await prompts.resolve(self.pool, 'morning', 900))[1:], ('common', version))
        await self.press(self.cq('pm:exit'))
        self.assertIsNone(await prompts.draft(self.pool, 901))

    async def test_not_editor_is_refused(self):
        await self.press(self.cq('pm:k:review', tg=123))
        self.assertIn('не подключён', self.bot.send_message.await_args.args[1])
        self.assertIsNone(await prompts.draft(self.pool, 123))


class MenuTests(unittest.TestCase):
    def test_keyboard_has_prompt_mode_instead_of_old_buttons(self):
        import demo_bot
        with patch.dict(os.environ, {'PROMPT_EDITORS': '901'}):
            texts = [b.text for row in demo_bot.head_menu(901).keyboard for b in row]
            self.assertEqual(texts, ['✏️ Режим промта', '↩️ Старая методика', '🔄 Вернуть сохранённую версию', 'ℹ️ Версия анализа'])
            texts = [b.text for row in demo_bot.head_menu(902).keyboard for b in row]
            self.assertNotIn('✏️ Режим промта', texts)
            self.assertNotIn('Сравнить методики', texts)
            self.assertNotIn('Объединённый разбор', texts)

    def test_prompt_text_handler_runs_before_free_questions(self):
        import demo_bot
        names = [h.callback.__name__ for h in demo_bot.router.message.handlers]
        self.assertLess(names.index('ask'), names.index('rop_question'))


if __name__ == '__main__':
    unittest.main()
