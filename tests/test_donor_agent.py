"""Агент доноров: номера и реестр, разбор страниц и выдачи, защита от внутренних адресов, полный прогон на макетах."""
import asyncio
import base64
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from crm import donor_agent as agent, donor_phone as ph, donor_web as webx
from tests.test_crm import DSN, HEAD, MANAGER, OTHER, DBCase
from tools import Forbidden


class PhoneTests(unittest.TestCase):
    def test_norm_and_format(self):
        self.assertEqual(ph.norm('8 (912) 345-67-89'), '79123456789')
        self.assertEqual(ph.fmt('79123456789'), '+7 912 345-67-89')
        self.assertEqual(ph.fmt('78001234567'), '8 800 123-45-67')
        self.assertFalse(ph.valid('71234567890'))
        self.assertEqual([n for n, _ in ph.find('Звоните 8 (861) 200-30-40, +7 918 111 22 33, ИНН 2310240077')], ['78612003040', '79181112233'])

    def test_operator_from_registry(self):
        self.assertEqual(ph.operator('79123456789')['op'], 'МТС')
        self.assertEqual(ph.operator('79123456789')['type'], 'mobile')
        mango = ph.operator('74991122334')
        self.assertTrue(mango['virtual'])  # Манго Телеком: ВАТС или колл-трекинг
        self.assertEqual(mango['type'], 'landline')


HTML = """<html><head><title>Кухни Альфа — кухни на заказ</title><meta name="description" content="Кухни в Краснодаре">
<script>var x = "+7 (999) 000-00-01"; calltouch.init()</script></head><body>
<header><a href="/contacts">Контакты</a> <a href="/team">Наша команда</a> <a href="/vacancy">Вакансии</a></header>
<p>Менеджер Ольга: <a href="tel:+79181112233">+7 918 111-22-33</a></p>
<div>Офис продаж: 8 (861) 200-30-40</div><a href="https://wa.me/79181112244">WhatsApp</a>
<a href="https://other.ru/contacts">чужие контакты</a></body></html>"""


class PageTests(unittest.TestCase):
    def test_parse_page(self):
        p = webx.parse_page('https://kuhni-a.ru/', 200, HTML)
        self.assertEqual(p['title'], 'Кухни Альфа — кухни на заказ')
        by = {x['n']: x for x in p['phones']}
        self.assertIn('текст', by['79181112233']['where'])
        self.assertIn('tel-ссылка', by['79181112233']['where'])
        self.assertIn('Ольга', by['79181112233']['near'])
        self.assertEqual(by['79990000001']['where'], ['код страницы'])  # только в коде страницы
        self.assertIn('WhatsApp', by['79181112244']['where'])
        self.assertIn('колл-трекинг Calltouch', p['tech'])
        self.assertEqual([x['url'] for x in p['contact_links']], ['https://kuhni-a.ru/contacts', 'https://kuhni-a.ru/team', 'https://kuhni-a.ru/vacancy'])
        self.assertNotIn('calltouch', p['text'])  # скрипты в текст не попадают

    def test_parse_yandex_xml(self):
        xml = ('<?xml version="1.0" encoding="utf-8"?><yandexsearch><response><results><grouping>'
               '<group><doc><url>https://kuhni-a.ru/</url><domain>kuhni-a.ru</domain><title>Кухни <hlword>Альфа</hlword></title>'
               '<passages><passage>Звоните +7 918 111-22-33</passage></passages></doc></group>'
               '<group><doc><url>https://www.avito.ru/x</url><domain>www.avito.ru</domain><title>Авито</title></doc></group>'
               '</grouping></results></response></yandexsearch>').encode()
        res = webx.parse_xml(xml)
        self.assertEqual([(r['host'], r['title']) for r in res], [('kuhni-a.ru', 'Кухни Альфа'), ('avito.ru', 'Авито')])
        self.assertIn('918', res[0]['snippet'])
        nothing = '<yandexsearch><response><error code="15">Искомая комбинация слов нигде не встречается</error></response></yandexsearch>'.encode()
        self.assertEqual(webx.parse_xml(nothing), [])

    def test_deferred_search_polls_operation(self):
        raw = base64.b64encode('<yandexsearch><response><results><grouping><group><doc><url>https://kuhni-a.ru/</url><title>Альфа</title></doc></group></grouping></results></response></yandexsearch>'.encode()).decode()
        polls = []

        def handler(request):
            if request.url.path.endswith('/searchAsync'):
                return httpx.Response(200, json={'id': 'op1', 'done': False})
            polls.append(1)
            return httpx.Response(200, json={'id': 'op1', 'done': len(polls) > 1, 'response': {'rawData': raw}} if len(polls) > 1 else {'id': 'op1', 'done': False})

        async def go():
            with patch.dict(os.environ, {'YANDEX_SEARCH_API_KEY': 'k', 'YANDEX_FOLDER_ID': 'f'}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                    return await webx.search_deferred(c, ['кухни'], wait=5, poll=0)
        got = asyncio.run(go())
        self.assertEqual([r['host'] for r in got['кухни']], ['kuhni-a.ru'])
        self.assertEqual(len(polls), 2)

    def test_deferred_search_reports_errors(self):
        def handler(request):
            if request.url.path.endswith('/searchAsync'):
                return httpx.Response(200, json={'id': 'op1', 'done': False})
            return httpx.Response(200, json={'id': 'op1', 'done': True, 'error': {'code': 7, 'message': 'Permission denied'}})

        async def go():
            with patch.dict(os.environ, {'YANDEX_SEARCH_API_KEY': 'k', 'YANDEX_FOLDER_ID': 'f'}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                    errors = {}
                    got = await webx.search_deferred(c, ['кухни'], wait=5, poll=0, errors=errors)
                    return got, errors
        got, errors = asyncio.run(go())
        self.assertIsNone(got['кухни'])
        self.assertEqual(errors['кухни'], '7 Permission denied')

    def test_internal_addresses_blocked(self):
        async def check(url):
            with self.assertRaises(webx.Blocked):
                await webx.check_url(url)
        for url in ('http://127.0.0.1/', 'http://10.0.0.5/', 'http://169.254.169.254/latest/meta-data', 'file:///etc/passwd',
                    'https://example.ru:8080/', 'http://[::1]/', 'https://user:pw@example.ru/', 'https://localhost/',
                    'https://online.sberbank.ru/'):
            asyncio.run(check(url))


class JudgeTests(unittest.TestCase):
    """Вердикт по номеру: 8-800 и чужие города при заданном городе, сайты «Кто звонил», каталоги."""

    def run_for(self, city='Сыктывкар'):
        r = agent.Run.__new__(agent.Run)
        r.brief = {'city': city, 'remote': False}
        r.state = {'seen': {}, 'dup': {}, 'dossiers': {}}
        return r

    def row(self, run, n, **kw):
        run.state['seen'][n] = {'u': ['https://a.ru/', 'https://b.ru/'], 's': {'текст': 1}, 'ctx': '', 'pre': ''}
        base = {'n': n, 'type': 'отдел продаж', 'who': '', 'person': False, 'stage': 'выбирает услугу', 'city': 'Сыктывкар',
                'co': ['Альфа'], 'real': {'альфа'}, 'sites': ['a.ru'], 'cverdict': 'берём', 'cwhy': '', 'scale': 'одна точка',
                'op': ph.operator(n), 'series': 1}
        base.update(kw)
        return base

    def test_800_is_test_when_city_set(self):
        run = self.run_for()
        j = run.judge(self.row(run, '78001234567', type='8-800'))
        self.assertEqual(j['v'], 'test')
        self.assertTrue(any('федеральная линия' in w for w in j['why']))
        run = self.run_for('вся Россия')
        self.assertEqual(run.judge(self.row(run, '78001234567', type='8-800'))['v'], 'go')

    def test_landline_in_other_city_is_no(self):
        run = self.run_for()
        self.assertEqual(run.judge(self.row(run, '74951234567', city='Москва'))['v'], 'no')
        self.assertEqual(run.judge(self.row(run, '79123456789', city='Москва'))['v'], 'test')  # мобильный может работать и тут

    def test_catalog_and_case_do_not_make_duplicates(self):
        run = self.run_for()
        n = '79123456789'
        run.state['seen'][n] = {'u': ['https://a.ru/'], 's': {'текст': 1}, 'ctx': '', 'pre': ''}
        run.state['dossiers'] = {
            'cat': {'status': 'done', 'name': 'Каталог Spravker', 'site': 'spravker.ru', 'kind': 'не донор', 'verdict': 'не брать',
                    'phones': [{'number': n, 'type': 'менеджер (мобильный)', 'stage': 'выбирает услугу', 'office_city': 'Сыктывкар'}]},
            'a': {'status': 'done', 'name': 'Полезный Юрист', 'site': 'a.ru', 'kind': 'прямой конкурент', 'verdict': 'берём',
                  'phones': [{'number': n, 'type': 'менеджер (мобильный)', 'stage': 'выбирает услугу', 'office_city': 'Сыктывкар'}]},
            'b': {'status': 'done', 'name': 'Полезный юрист', 'site': 'a.ru', 'kind': 'прямой конкурент', 'verdict': 'берём',
                  'phones': [{'number': n, 'type': 'менеджер (мобильный)', 'stage': 'выбирает услугу', 'office_city': 'Сыктывкар'}]}}
        row = run.phone_rows()[0]
        self.assertEqual(row['real'], {'полезныйюрист'})
        self.assertEqual(row['cverdict'], 'берём')  # вердикт настоящей компании, не каталога
        self.assertEqual(row['j']['v'], 'go')

    def test_who_called_sites_are_not_other_companies(self):
        run = self.run_for()
        n = '79123456789'
        row = self.row(run, n)
        run.phone_rows = lambda: [dict(row, j={'v': 'go'})]
        run.save = lambda: asyncio.sleep(0)
        run.set_stage = lambda text: asyncio.sleep(0)

        async def batch(queries, what):
            return {queries[0]: [{'title': 'Кто звонил с +79123456789: 6 отзывов', 'url': 'https://zvonili.com/x', 'host': 'zvonili.com', 'snippet': ''},
                                 {'title': 'Чужая фирма +7 912 345-67-89', 'url': 'https://zzz.ru/', 'host': 'zzz.ru', 'snippet': ''}]}
        run.batch_search = batch
        asyncio.run(run.check_dups())
        d = run.state['dup'][n]
        self.assertEqual(d['others'], ['Чужая фирма +7 912 345-67-89'])
        self.assertTrue(d['called'])
        self.assertIn('с номера звонят сами (есть на сайтах «Кто звонил»)', run.proofs(row))


# ------------------------------------------------------------------ полный прогон на макетах

SITES = {
    'https://kuhni-a.ru/': HTML,
    'https://kuhni-a.ru/contacts': '<p>Отдел кадров: +7 918 555-00-11</p><p>Менеджер Ольга +7 918 111-22-33</p>',
    'https://kuhni-a.ru/team': '<p>Иван, менеджер по продажам: +7 912 345-67-89</p>',
    'https://kuhni-b.ru/': '<p>Кухни Бета. Телефон +7 495 123-45-67</p>',
}


class FakeAI:
    """Модель по шагам: план, разбор сайтов (номера из присланных данных), досье без номеров, конец поиска."""

    def __init__(self):
        self.calls = []

    async def create(self, model, max_tokens, system, messages, tools):
        self.calls.append(system[-80:])
        names = [t['name'] for t in tools]
        user = messages[0]['content']
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)

        def use(name, args):
            return SimpleNamespace(content=[SimpleNamespace(type='tool_use', id='t1', name=name, input=args)], usage=usage, stop_reason='end_turn')
        if 'save_plan' in names:
            return use('save_plan', {'variants': ['Кухни на заказ'], 'buyer': 'кто покупает кухню', 'remote': False, 'avoid': ['ТЦ'], 'note': '',
                                     'segments': [{'name': 'Конкуренты', 'type': 'прямой конкурент', 'who_calls': 'покупатели кухонь', 'where': 'сайты',
                                                   'queries': ['кухни на заказ краснодар', 'кухни краснодар'], 'strong': True}]})
        if 'save_sites' in names:
            sites = []
            for block in user.split('### ')[1:]:
                host = block.split('\n')[0].strip()
                phones = [{'number': m, 'type': 'служебный' if 'кадр' in line else 'менеджер (мобильный)',
                           'who': 'Иван, менеджер' if '345' in m else 'Пётр Сидоров' if '22-44' in m else '', 'person': '345' in m or '22-44' in m, 'caller': 'покупатели', 'stage': 'выбирает услугу',
                           'office_city': 'Краснодар', 'source': 'https://' + host + '/'}
                          for line in block.split('\n') if line.startswith('- ') for m in [line[2:].split(' [')[0]]]
                sites.append({'domain': host, 'name': host, 'kind': 'прямой конкурент', 'relevance': 8, 'flow': 7, 'verdict': 'берём', 'why': 'кухни',
                              'city': 'Краснодар', 'scale': 'одна точка', 'phones': phones})
            return use('save_sites', {'sites': sites})
        if 'save_dossier' in names:
            return use('save_dossier', {'name': 'x', 'site': '', 'city': '', 'kind': 'прямой конкурент', 'relevance': 8, 'flow': 7, 'verdict': 'берём',
                                        'why': 'ок', 'scale': 'одна точка', 'phones': []})
        return use('finish_search', {'summary': 'Рынок кухонь Краснодара.'})


async def fake_search(client, query, region=225, results=20):
    if query.startswith('"'):
        if '495' in query:  # номер Беты указан ещё и у другой компании
            return [{'title': 'Другая фирма +7 495 123-45-67', 'url': 'https://zzz.ru/', 'host': 'zzz.ru', 'snippet': ''}]
        return []
    return [{'title': 'Кухни Альфа', 'url': 'https://kuhni-a.ru/', 'host': 'kuhni-a.ru', 'snippet': ''},
            {'title': 'Авито', 'url': 'https://www.avito.ru/kuhni', 'host': 'avito.ru', 'snippet': ''},
            {'title': 'Кухни Бета', 'url': 'https://kuhni-b.ru/', 'host': 'kuhni-b.ru', 'snippet': ''}]


SYNC_CALLS = []


async def fake_deferred(client, queries, region=225, results=20, wait=0, poll=0, on_wait=None, errors=None):
    if on_wait:
        await on_wait(len(queries) - 1, len(queries))
    if errors is not None and 'кухни краснодар' in queries:
        errors['кухни краснодар'] = 'не дождались'
    return {q: None if q == 'кухни краснодар' else await fake_search(client, q) for q in queries}  # один запрос не успел


async def counted_search(client, query, region=225, results=20):
    SYNC_CALLS.append(query)
    return await fake_search(client, query)


async def fake_fetch(client, url):
    if url not in SITES:
        raise webx.Blocked('нет страницы')
    return {'url': url, 'status': 200, 'html': SITES[url]}


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class RunTests(DBCase):
    async def test_full_run_on_mocks(self):
        fake = FakeAI()
        env = {'ANTHROPIC_API_KEY': 'k', 'YANDEX_SEARCH_API_KEY': 'k', 'YANDEX_FOLDER_ID': 'f', 'DONOR_DAILY_RUB': '1500'}
        with patch.dict(os.environ, env), patch.object(agent, 'ai_client', lambda p: SimpleNamespace(messages=fake)), \
                patch.object(webx, 'search', counted_search), patch.object(webx, 'fetch', fake_fetch), \
                patch.object(webx, 'search_deferred', fake_deferred), patch.object(agent.Run, 'usd_rub', lambda self: asyncio.sleep(0, 100.0)):
            SYNC_CALLS.clear()
            started = await agent.start(self.pool, MANAGER, {'niche': 'Кухни на заказ', 'city': 'Краснодар', 'target': 10})
            row = await agent.take(self.pool)
            self.assertEqual(row['id'], started['id'])
            await agent.process(self.pool, row, None)
            run = await agent.one(self.pool, MANAGER, started['id'])
        self.assertEqual(run['status'], 'done', run['error'])
        phones = {p['n']: p for p in run['result']['phones']}
        self.assertEqual(phones['79123456789']['verdict'], 'go')  # мобильный менеджера с именем
        self.assertTrue(phones['79123456789']['live'])
        self.assertFalse(any('Пётр' in w for w in phones['79181112244']['why']))  # имя выдумано: рядом с номером его нет
        self.assertTrue(phones['79181112244']['live'])  # но WhatsApp — признак живого человека
        self.assertEqual(phones['79185550011']['verdict'], 'no')  # отдел кадров
        self.assertEqual(phones['74951234567']['verdict'], 'test')  # встречается у другой компании
        self.assertTrue(any('у других' in w for w in phones['74951234567']['why']))
        self.assertEqual(phones['79990000001']['verdict'], 'no')  # только в коде страницы, на сайте колл-трекинг
        self.assertIn('колл-трекинг', phones['79990000001']['why'][0])
        self.assertFalse(any(s['site'] == 'avito.ru' for s in run['result']['sites']))
        self.assertEqual(SYNC_CALLS, ['кухни краснодар'])  # обычным поиском только то, что отложенный не успел
        self.assertGreater(run['rub'], 0)
        self.assertEqual(set(run['cost']), {'план', 'разбор сайтов', 'проверка повторов', 'досье', 'поиск кандидатов', 'обход выдачи'})
        self.assertAlmostEqual(sum(run['cost'].values()), run['rub'], delta=0.1)
        self.assertEqual(await self.pool.fetchval('SELECT calls FROM donor_spend'), len(fake.calls))
        listing = await agent.overview(self.pool, OTHER)  # прогоны видят все в отделе
        self.assertEqual(listing['runs'][0]['good'], run['result']['good'])
        self.assertFalse(listing['runs'][0]['can_manage'])
        with self.assertRaises(Forbidden):
            await agent.delete(self.pool, OTHER, started['id'])
        await agent.delete(self.pool, HEAD, started['id'])
        self.assertEqual((await agent.overview(self.pool, MANAGER))['runs'], [])

    async def test_limits_and_stop(self):
        env = {'ANTHROPIC_API_KEY': 'k', 'YANDEX_SEARCH_API_KEY': 'k', 'YANDEX_FOLDER_ID': 'f'}
        with patch.dict(os.environ, env):
            with self.assertRaises(ValueError):
                await agent.start(self.pool, MANAGER, {'niche': 'Ок', 'target': 20})
            first = await agent.start(self.pool, MANAGER, {'niche': 'Кухни', 'target': 20})
            await agent.stop(self.pool, MANAGER, first['id'])
            self.assertEqual((await agent.one(self.pool, MANAGER, first['id']))['status'], 'stopped')
            await self.pool.execute("INSERT INTO donor_spend(day, rub) VALUES(current_date, 1500)")
            with self.assertRaises(ValueError) as e:
                await agent.start(self.pool, MANAGER, {'niche': 'Кухни', 'target': 20})
            self.assertIn('лимит', str(e.exception))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': '', 'OCTAV_API_KEY': ''}):
            with self.assertRaises(ValueError):
                await agent.start(self.pool, MANAGER, {'niche': 'Кухни', 'target': 20})
        with self.assertRaises(Forbidden):
            await agent.settings(self.pool, MANAGER, {'avito_text': 'Здравствуйте!'})
        await agent.settings(self.pool, HEAD, {'avito_text': 'Здравствуйте! Удобно обсудить в Телеграм @AveronCompany'})
        self.assertIn('@AveronCompany', (await agent.overview(self.pool, MANAGER))['avito_text'])

    async def test_cheap_yandex_model_for_sites(self):
        import json as _json
        answers = [httpx.Response(200, json={'usage': {'prompt_tokens': 10000, 'completion_tokens': 1000}, 'choices': [{'message': {'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'save_sites', 'arguments': _json.dumps({'sites': [{'domain': 'kuhni-a.ru'}]})}}]}}]}),
            httpx.Response(500, text='упало')]
        sent = []

        def handler(request):
            sent.append(_json.loads(request.content))
            return answers.pop(0)
        with patch.dict(os.environ, {'YANDEX_GPT_API_KEY': 'k', 'YANDEX_FOLDER_ID': 'fold', 'ANTHROPIC_API_KEY': 'k', 'YANDEX_SEARCH_API_KEY': 'k'}):
            started = await agent.start(self.pool, MANAGER, {'niche': 'Кухни', 'target': 10, 'sites_model': 'aliceai-llm-flash'})
            row = await self.pool.fetchrow('SELECT * FROM donor_runs WHERE id=$1', started['id'])
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                run = agent.Run(self.pool, row, http)
                self.assertEqual(await run.yandex_sites('сайты'), [{'domain': 'kuhni-a.ru'}])
                self.assertIsNone(await run.yandex_sites('сайты'))  # ошибка: разберёт основная модель
            with self.assertRaises(ValueError):
                await agent.start(self.pool, MANAGER, {'niche': 'Кухни', 'target': 10, 'sites_model': 'gpt-что-угодно'})
        self.assertEqual(sent[0]['model'], 'gpt://fold/aliceai-llm-flash')
        self.assertAlmostEqual(run.rub, 1.2)  # 10k входа по 0,1 ₽ и 1k выхода по 0,2 ₽
        self.assertIn('Alice', (await agent.one(self.pool, MANAGER, started['id']))['sites_model'])

    async def test_reserve_model_takes_over(self):
        class NoKey(Exception):
            status_code = 401

        class Down:
            async def create(self, **kw):
                raise NoKey('нет ключа')
        fake = FakeAI()
        clients = {'dreamn': SimpleNamespace(messages=Down()), 'octav': SimpleNamespace(messages=fake)}
        env = {'ANTHROPIC_API_KEY': 'k', 'OCTAV_API_KEY': 'k2'}
        with patch.dict(os.environ, env), patch.object(agent, 'ai_client', lambda p: clients[p['name']]):
            await self.pool.execute("INSERT INTO donor_runs(id, client_id, niche) VALUES(77, 1, 'Кухни')")
            row = await self.pool.fetchrow('SELECT * FROM donor_runs WHERE id=77')
            run = agent.Run(self.pool, row, None)
            content = await run.ask('x save_plan', [{'role': 'user', 'content': 'hi'}], [{'name': 'save_plan', 'description': '', 'input_schema': {}}])
        self.assertEqual(content[0]['name'], 'save_plan')
        self.assertEqual(run.provider, 1)
        self.assertTrue(any('запасная' in x['text'] for x in run.log_items))


if __name__ == '__main__':
    unittest.main()
