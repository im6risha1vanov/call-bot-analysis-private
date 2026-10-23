"""AveronGPT: кто что видит, запасная модель, лимит клиента, разметка ответа. Модели подменены, данные вымышленные."""
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
os.environ.setdefault('ENCRYPTION_KEY', 'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE=')

from crm import auth, gpt, robot_store
from tests.test_crm import DSN, HEAD, MANAGER, DBCase, fake_values
from tools import Forbidden

CLIENT = auth.Viewer('client', None, 'Кухни')
DIRECTOR = auth.Viewer('director', HEAD)
STAFF = auth.Viewer('manager', MANAGER)


def reply(text='', calls=None, tokens=1000):
    return {'text': text, 'calls': calls or [], 'tokens': tokens, 'in': tokens, 'out': 0}


class PureTests(unittest.TestCase):
    def test_render_escapes_and_keeps_only_allowed_buttons(self):
        html = gpt.render('Смотрите **дозвон** <script>x</script>\n- раз\n- два\n[[Открыть кабинет|cab]] и [[Сводка|summary]]', {'cab'})
        self.assertIn('<b>дозвон</b>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<ul><li>раз</li><li>два</li></ul>', html)
        self.assertIn('<button class="gpt-go" type="button" data-go="cab">Открыть кабинет</button>', html)
        self.assertNotIn('data-go="summary"', html)  # раздела нет у этой роли: остаётся текстом
        self.assertIn('Сводка', html)
        self.assertIn('data-arg="contracts"', gpt.render('[[Договоры|deals/contracts]]', {'deals'}))

    def test_guide_by_role(self):
        client = gpt.guide('client', robot=False)
        self.assertIn('Кабинет (cab)', client)
        for word in ('Сводка (summary)', 'Себестоимость', 'себестоимость', 'Очередь (queue)', 'Робот (robot)'):
            self.assertNotIn(word, client)
        self.assertIn('Робот (robot)', gpt.guide('client', robot=True))
        self.assertNotIn('Настройки (settings)', gpt.guide('manager', robot=False))
        self.assertIn('Сводка (summary)', gpt.guide('director', robot=True))

    def test_tools_by_role(self):
        names = lambda who, pages: {t['name'] for t in gpt.tool_schemas(who, pages)}
        self.assertEqual(names(CLIENT, {'cab'}), {'my_cabinet'})
        self.assertEqual(names(CLIENT, {'cab', 'robot'}), {'my_cabinet', 'robot_stats'})
        self.assertNotIn('callcenter', names(STAFF, {'calls'}))
        self.assertIn('calls_question', names(STAFF, {'calls'}))
        self.assertTrue({'callcenter', 'client_cabinet', 'robot_stats'} <= names(DIRECTOR, {'robot'}))
        prompt = gpt.system_prompt(CLIENT, {'cab'}, '2026-10-24', '')
        self.assertIn('не рассказывай о других клиентах', prompt)
        self.assertNotIn('calls_question', prompt)

    def test_models_chain(self):
        with patch.dict(os.environ, {'AVERONGPT_MODELS': 'dreamn:gpt-6.1-sol, yandex:yandexgpt-5.1, bad:x'}):
            self.assertEqual(gpt.models(), [('dreamn', 'gpt-6.1-sol'), ('yandex', 'yandexgpt-5.1')])
        with patch.dict(os.environ, {'AVERONGPT_MODELS': ''}):
            self.assertEqual(gpt.models()[0], ('yandex', 'yandexgpt-5.1'))

    def test_yandex_messages_keep_tool_calls(self):
        turns = [{'role': 'user', 'text': 'q'}, {'role': 'assistant', 'text': '', 'calls': [{'id': 'c1', 'name': 'my_cabinet', 'args': {}}]},
                 {'role': 'tool', 'id': 'c1', 'name': 'my_cabinet', 'text': '{}'}]
        msgs = gpt.openai_messages('sys', turns)
        self.assertEqual([m['role'] for m in msgs], ['system', 'user', 'assistant', 'tool'])
        self.assertEqual(msgs[2]['tool_calls'][0]['function']['name'], 'my_cabinet')
        a = gpt.anthropic_messages(turns)
        self.assertEqual(a[1]['content'][0]['type'], 'tool_use')
        self.assertEqual(a[2]['content'][0]['tool_use_id'], 'c1')


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class GptTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from pathlib import Path
        await self.pool.execute((Path(__file__).resolve().parents[1] / 'migrations/029_averongpt.sql').read_text())
        await self.pool.execute('TRUNCATE crm_gpt_spend')
        await robot_store.set_option(self.pool, 'gpt_usd_rub', {'day': __import__('datetime').date.today().isoformat(), 'value': 100})
        self.env = patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads', 'AVERONGPT_MODELS': 'yandex:yandexgpt-5.1,dreamn:gpt-6.1-sol'})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await super().asyncTearDown()

    async def test_client_gets_own_cabinet_through_tool(self):
        seen = []

        async def yandex(model, system, turns, tools):
            seen.append(turns[-1])
            if turns[-1]['role'] == 'user':
                return reply(calls=[{'id': 'c1', 'name': 'my_cabinet', 'args': {'client': 'Окна'}}])
            data = json.loads(turns[-1]['text'])
            return reply(f"Передано **{data['итог по номерам']['total']}** номеров. [[Кабинет|cab]] [[Сводка|summary]]")
        with patch.dict(gpt.BACKENDS, {'yandex': yandex}), patch('crm.gsheets.values', fake_values):
            got = await gpt.ask(self.pool, CLIENT, 'Сколько номеров?', 'cab', 'Номеров передано 4')
        self.assertIn('Передано <b>4</b> номеров', got['html'])  # свой лист «Кухни», чужое имя от модели не действует
        self.assertIn('data-go="cab"', got['html'])
        self.assertNotIn('data-go="summary"', got['html'])
        self.assertIn('Номеров передано 4', seen[0]['text'])  # текст с экрана ушёл вместе с вопросом
        row = await self.pool.fetchrow('SELECT * FROM crm_questions')
        self.assertEqual((row['role'], row['client_key'], row['page'], row['model']), ('client', 'Кухни', 'cab', 'yandex:yandexgpt-5.1'))
        self.assertAlmostEqual(await gpt.spent_today(self.pool, 'client:Кухни'), 1.6, places=2)  # 2 обращения × 1000 токенов × 0,8 ₽
        self.assertEqual([x['question'] for x in await gpt.history(self.pool, CLIENT)], ['Сколько номеров?'])
        self.assertEqual(await gpt.history(self.pool, auth.Viewer('client', None, 'Окна')), [])

    async def test_fallback_model_continues(self):
        async def down(*a):
            raise gpt.Unavailable('503')
        dreamn = AsyncMock(return_value=reply('Ответ запасной модели'))
        with patch.dict(gpt.BACKENDS, {'yandex': down, 'dreamn': dreamn}):
            got = await gpt.ask(self.pool, DIRECTOR, 'Где сделки?')
        self.assertIn('Ответ запасной модели', got['html'])
        self.assertEqual(await self.pool.fetchval('SELECT model FROM crm_questions'), 'dreamn:gpt-6.1-sol')
        with patch.dict(gpt.BACKENDS, {'yandex': down, 'dreamn': down}):
            got = await gpt.ask(self.pool, DIRECTOR, 'Где сделки?')
        self.assertIn('модель не отвечает', got['html'])

    async def test_client_daily_limit_staff_unlimited(self):
        await gpt.add_spend(self.pool, 'client:Кухни', 200.0)  # $2 по курсу 100
        model = AsyncMock(return_value=reply('ок'))
        with patch.dict(gpt.BACKENDS, {'yandex': model}):
            got = await gpt.ask(self.pool, CLIENT, 'Привет')
            self.assertIn('закончились', got['html'])
            model.assert_not_called()
            await gpt.ask(self.pool, DIRECTOR, 'Привет')
            await gpt.ask(self.pool, STAFF, 'Привет')
        self.assertEqual(model.await_count, 2)

    async def test_tools_respect_access(self):
        with self.assertRaises(Forbidden):
            await gpt.run_tool(self.pool, CLIENT, 'sales_summary', {})
        with self.assertRaises(Forbidden):
            await gpt.run_tool(self.pool, CLIENT, 'robot_stats', {})  # робот клиенту не включён
        with self.assertRaises(Forbidden):
            await gpt.run_tool(self.pool, STAFF, 'callcenter', {})
        summary = json.loads(await gpt.run_tool(self.pool, STAFF, 'sales_summary', {'period': '7', 'manager_ext': '102'}))
        self.assertEqual([m['ext'] for m in summary['менеджеры']], ['101'])  # менеджеру только свои цифры
        await robot_store.set_option(self.pool, 'clients', ['Кухни'])
        stats = json.loads(await gpt.run_tool(self.pool, CLIENT, 'robot_stats', {'owner': ''}))
        self.assertEqual(stats['total'], 0)

    async def test_old_questions_show_in_history_and_clear(self):
        await self.pool.execute("INSERT INTO crm_questions(client_id,role,extension,question,answer) VALUES(1,'manager','101','старый','<b>да</b>')")
        items = await gpt.history(self.pool, STAFF)
        self.assertEqual((items[0]['question'], items[0]['html']), ('старый', '<b>да</b>'))
        self.assertEqual(await gpt.history(self.pool, auth.Viewer('manager', __import__('tools').Actor(124, 1, 'manager', '102'))), [])
        await gpt.clear(self.pool, STAFF)
        self.assertEqual(await gpt.history(self.pool, STAFF), [])


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class GptWebTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from pathlib import Path
        await self.pool.execute((Path(__file__).resolve().parents[1] / 'migrations/029_averongpt.sql').read_text())
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()
        await super().asyncTearDown()

    async def session(self, role, client_key=None, test=False):
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,client_key,is_test) VALUES($1,now()+'1 day',$2,1,$3,$4)",
                                auth.digest('s-' + role), role, client_key, test)
        self.http.session.cookie_jar.update_cookies({'crm_session': 's-' + role})

    async def test_client_asks_over_http(self):
        h = {'X-CRM': '1'}
        self.assertEqual((await self.http.get('/api/gpt')).status, 401)
        await self.session('client', 'Кухни')
        self.assertEqual((await (await self.http.get('/api/gpt')).json())['items'], [])
        with patch.dict(gpt.BACKENDS, {'yandex': AsyncMock(return_value=reply('Здесь ваш кабинет'))}), \
                patch.dict(os.environ, {'AVERONGPT_MODELS': 'yandex:yandexgpt-5.1'}):
            r = await self.http.post('/api/gpt', json={'question': 'Что это?', 'page': 'cab', 'page_text': 'x'}, headers=h)
        self.assertEqual(r.status, 200, await r.text())
        self.assertIn('Здесь ваш кабинет', (await r.json())['html'])
        self.assertEqual((await self.http.post('/api/gpt', json={'question': ''}, headers=h)).status, 400)
        self.assertEqual((await self.http.post('/api/gpt/clear', json={}, headers=h)).status, 200)

    async def test_view_only_cannot_ask(self):
        await self.session('director', test=True)
        r = await self.http.post('/api/gpt', json={'question': 'x'}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 403)
