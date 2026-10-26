"""Кабинет голосового робота в CRM: операторы, контакты, стоп-лист, кампании по расписанию, звонки, аналитика.

Робот подменён маленьким HTTP-сервером (/status, /dial), его журнал звонков — временным файлом.
"""
import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_crm import DSN, TEST_KEY, DBCase, WebTests, fake_values

from crm import auth, robot, robot_runner, robot_store

H = {'X-CRM': '1'}


def xlsx(rows) -> bytes:
    """Минимальный .xlsx: общие строки и один лист."""
    strings, sheet = [], []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, value in enumerate(row):
            ref = chr(65 + j) + str(i)
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet.append(f'<row r="{i}">{"".join(cells)}</row>')
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('xl/sharedStrings.xml', f'<sst xmlns="{ns}">' + ''.join(f'<si><t>{s}</t></si>' for s in strings) + '</sst>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(sheet)}</sheetData></worksheet>')
    return buf.getvalue()


class ParseTest(unittest.TestCase):
    def test_xlsx_and_csv_contacts(self):
        rows = robot_store.xlsx_rows(xlsx([['ФИО', 'Телефон', 'Комментарий'], ['Иван', 79990001122, 'кухня'], ['Пётр', '8 (999) 000-11-33', '']]))
        contacts, bad = robot_store.contacts_from_rows(rows)
        self.assertEqual([(c['phone'], c['name'], c['note']) for c in contacts],
                         [('79990001122', 'Иван', 'кухня'), ('79990001133', 'Пётр', '')])
        contacts, bad = robot_store.contacts_from_rows(robot_store.csv_rows('89990001144;Анна\nнет номера;x\n+7 999 000 11 44;Анна\n'))
        self.assertEqual(([c['phone'] for c in contacts], bad), (['79990001144'], 1))
        with self.assertRaises(ValueError):
            robot_store.xlsx_rows(b'not a zip')

    def test_window(self):
        c = {'timezone': 'Asia/Yekaterinburg', 'days': [1, 2, 3, 4, 5], 'start_time': datetime(2026, 1, 1, 10).time(),
             'end_time': datetime(2026, 1, 1, 19).time()}
        # пятница 17.10.2026 06:00 UTC = 11:00 в Екатеринбурге
        self.assertTrue(robot_store.in_window(c, datetime(2026, 10, 16, 6, tzinfo=timezone.utc)))
        self.assertFalse(robot_store.in_window(c, datetime(2026, 10, 16, 3, tzinfo=timezone.utc)))  # 08:00
        self.assertFalse(robot_store.in_window(c, datetime(2026, 10, 17, 6, tzinfo=timezone.utc)))  # суббота


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class CabinetTests(DBCase):
    enter = WebTests.enter

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        root = Path(__file__).resolve().parents[1]
        await self.pool.execute((root / 'migrations' / '020_robot.sql').read_text())
        await self.pool.execute('TRUNCATE robot_contacts, robot_stoplist, robot_campaigns, robot_targets, robot_calls, robot_options CASCADE')
        from aiohttp import web as aweb
        from aiohttp.test_utils import TestServer
        from voice import operator as vop
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / 'calls.jsonl'
        self.dialed, self.busy = [], {'active': 0, 'pending': 0}

        self.dial_fail = None  # (статус, ошибка): робот отказал в звонке

        async def fake_dial(request):
            self.dialed.append(await request.json())
            if self.dial_fail:
                return aweb.json_response({'error': self.dial_fail[1]}, status=self.dial_fail[0])
            return aweb.json_response({'id': f'call-{len(self.dialed)}'})

        async def fake_status(request):
            return aweb.json_response(self.busy)
        fake = aweb.Application()
        fake.router.add_post('/dial', fake_dial)
        fake.router.add_get('/status', fake_status)
        self.robot = TestServer(fake)
        await self.robot.start_server()
        for p in (patch.object(robot, 'DATA', self.tmp), patch.object(robot, 'CALLS_LOG', self.log),
                  patch.object(vop, 'EXTRA_DIR', self.tmp / 'operators'), patch.object(robot, 'api_port', lambda: self.robot.port),
                  patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'KITCHEN_SHEET_ID': 'leads', 'VOICE_SHEET_ID': ''}),
                  patch('crm.gsheets.values', fake_values)):
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.robot.close()
        await super().asyncTearDown()

    async def as_director(self):
        await self.http.get('/p/' + await auth.invite_create(self.pool, 1, 'director', tg='901'))

    def call_line(self, **rec):
        base = {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'operator': 'kitchen', 'mode': 'телефон', 'outcome': 'ndz', 'comment': '',
                'facts': {}, 'duration_s': 0, 'cost': 0, 'cost_unit': 'ток.', 'transcript': []}
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({**base, **rec}, ensure_ascii=False) + '\n')

    async def test_director_campaign_end_to_end(self):
        await self.as_director()
        st = await (await self.http.get('/api/robot/cab')).json()
        self.assertTrue(st['enabled'])
        self.assertIn('kitchen', [o['id'] for o in st['operators']])

        # встроенного директор правит прямо: правка под тем же id, заготовки остаются, «Вернуть исходный» её убирает
        from voice.operator import load
        r = await self.http.post('/api/robot/cab/operators/kitchen', json={'name': 'Ольга'}, headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(((await r.json())['builtin'], (await r.json())['edited']), (True, True))
        self.assertEqual(load('kitchen').name, 'Ольга')
        self.assertTrue(load('kitchen').quick)
        st = await (await self.http.get('/api/robot/cab')).json()
        self.assertEqual([o['edited'] for o in st['operators'] if o['id'] == 'kitchen'], [True])
        r = await self.http.post('/api/robot/cab/operators/kitchen/delete', json={}, headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(load('kitchen').name, 'Мария')
        r = await self.http.post('/api/robot/cab/operators/kitchen/delete', json={}, headers=H)
        self.assertEqual(r.status, 400)

        # копия правится и пишется TOML для робота
        r = await self.http.post('/api/robot/cab/operators', json={'copy_from': 'kitchen', 'name': 'Ольга', 'rules': ['Не называй цены.'],
                                                                     'fields': [{'key': '', 'text': 'Город: где кухня'}]}, headers=H)
        self.assertEqual(r.status, 200, await r.text())
        op = await r.json()
        self.assertEqual((op['name'], op['rules'], op['fields']), ('Ольга', ['Не называй цены.'], [{'key': 'f1', 'text': 'Город: где кухня'}]))
        self.assertEqual(load(op['id']).name, 'Ольга')
        r = await self.http.post('/api/robot/cab/operators', json={'copy_from': 'kitchen', 'plan': []}, headers=H)
        self.assertIn('план', (await r.json())['error'])

        # контакты из CSV и с листа таблицы, стоп-лист
        csv_text = 'Телефон;Имя\n89990000011;Анна\n89990000012;Борис\n89990000013;Вера\n'
        r = await self.http.post('/api/robot/cab/contacts/upload', json={'name': 'base.csv', 'text': csv_text}, headers=H)
        self.assertEqual((await r.json())['total'], 3)
        tabs = await (await self.http.get('/api/robot/cab/contacts/tabs')).json()
        self.assertEqual(tabs, {'tabs': []})  # без Google: список листов не получен, но страница не падает
        await self.http.post('/api/robot/cab/stop', json={'phones': '8 999 000 00 13'}, headers=H)

        # кампания: расписание, все контакты, запуск
        r = await self.http.post('/api/robot/cab/campaigns', json={'name': 'Тест', 'operator': op['id'], 'days': [1, 2, 3, 4, 5, 6, 7],
                                                                     'start_time': '00:00', 'end_time': '23:59', 'attempts': 2,
                                                                     'retry_minutes': 10, 'contacts': 'all'}, headers=H)
        camp = await r.json()
        self.assertEqual(camp['total'], 3)
        r = await self.http.post('/api/robot/cab/campaigns', json={'name': 'x', 'operator': op['id'], 'days': [], 'contacts': 'all'}, headers=H)
        self.assertEqual(r.status, 400)
        await self.http.post(f"/api/robot/cab/campaigns/{camp['id']}/start", json={}, headers=H)

        # расписание: робот занят — не звоним; свободен — звоним по одному, стоп-лист пропускаем
        self.busy['active'] = 1
        self.assertIsNone(await robot_runner.tick(self.pool))
        self.busy['active'] = 0
        first = await robot_runner.tick(self.pool)
        self.assertEqual((first['phone'], first['campaign_id'], first['owner'], first['operator']), ('79990000011', camp['id'], '', op['id']))
        self.assertEqual(self.dialed[0]['target_id'], first['target_id'])
        self.assertEqual((await robot_runner.tick(self.pool))['phone'], '79990000012')
        self.assertIsNone(await robot_runner.tick(self.pool))  # третий в стоп-листе, первые двое ждут итога

        # итоги из журнала робота: недозвон уходит на повтор, заинтересованный закрыт, «не звонить» — в стоп-лист
        meta = {'campaign_id': camp['id'], 'owner': ''}
        self.call_line(call_id='a1', phone='79990000011', outcome='ndz', target_id=first['target_id'], **meta)
        self.call_line(call_id='a2', phone='79990000012', outcome='interested', duration_s=120, cost=3000, tts_chars=2000,
                       target_id=self.dialed[1]['target_id'], recording='a2.wav', transcript=[['robot', 'Здравствуйте']], **meta)
        self.call_line(call_id='b1', phone='79990000099', outcome='do_not_call')
        self.call_line(mode='браузер', phone='')
        self.assertEqual(await robot_runner.ingest(self.pool), 3)
        self.assertEqual(await robot_runner.ingest(self.pool), 0)  # повторно не переносит
        targets = (await (await self.http.get(f"/api/robot/cab/campaigns/{camp['id']}/targets")).json())['items']
        self.assertEqual([(t['phone'], t['done'], bool(t['next_at'])) for t in targets],
                         [('79990000011', False, True), ('79990000012', True, False), ('79990000013', True, False)])
        stop = (await (await self.http.get('/api/robot/cab/stop')).json())['items']
        self.assertEqual(sorted(s['phone'] for s in stop), ['79990000013', '79990000099'])

        # звонки, фильтр, CSV, запись
        (self.tmp / 'recordings').mkdir()
        (self.tmp / 'recordings' / 'a2.wav').write_bytes(b'RIFF0000WAVE')
        calls = (await (await self.http.get('/api/robot/cab/calls')).json())['items']
        self.assertEqual(len(calls), 3)
        only = (await (await self.http.get('/api/robot/cab/calls?outcome=interested')).json())['items']
        self.assertEqual([(c['id'], c['recording'], c['campaign']) for c in only], [('a2', True, 'Тест')])
        csv_out = await (await self.http.get('/api/robot/cab/calls.csv')).text()
        self.assertIn('79990000012;Тест;Заинтересованный;120', csv_out)
        r = await self.http.get('/api/robot/cab/recording/a2')
        self.assertEqual(r.status, 200)

        # аналитика и цены: себестоимость считает CRM, директор задаёт только цену минуты каждому клиенту
        await robot_store.set_option(self.pool, 'prices_auto', {'mango_min': {'value': 1.5}, 'eleven_1k_chars': {'value': 10},
                                                                'llm_1k_tokens': {'value': 1}})
        r = await self.http.post('/api/robot/cab/options', json={'clients': ['Кухни'], 'prices': {
            'mango_min': 99, 'client_min': {'Кухни': '12,5', 'Окна': ''}}}, headers=H)
        got = await r.json()
        self.assertEqual(got['prices'], {'mango_min': 1.5, 'eleven_1k_chars': 10.0, 'llm_1k_tokens': 1.0, 'tts_1k_chars': 10.0,
                                         'client_min': {'Кухни': 12.5}})
        for bad in (-1, 'дорого', 5000):
            r = await self.http.post('/api/robot/cab/options', json={'prices': {'client_min': {'Кухни': bad}}}, headers=H)
            self.assertEqual(r.status, 400)
        st = await (await self.http.get('/api/robot/cab')).json()
        self.assertEqual(st['options']['auto']['mango_min']['value'], 1.5)
        econ = st['options']['econ']
        # 2 минуты разговора, 2000 символов и 3000 токенов: 1000 симв. и 1500 ток. на минуту;
        # распознавание: два звонка по 60 с = 8 кусков по 15 с × 0,1626 ₽ на 2 минуты
        self.assertEqual((econ['cost_min'], econ['parts'], econ['chars_min'], econ['tokens_min']),
                         (13.65, {'mango': 1.5, 'synth': 10.0, 'llm': 1.5, 'asr': 0.65}, 1000, 1500))
        # наборы: модель везде Pro 0,8; синтез v4 по 10, v4 Turbo вдвое дешевле, Яндекс 0,1626 ₽ за фразу ~60 симв.
        self.assertEqual(econ['presets'], {'quality': 13.35, 'balance': 8.35, 'economy': 6.06})
        self.assertEqual(econ['typical'], {'calls': 3, 'reach': 67, 'minutes_per_talk': 1.0})
        self.assertEqual(econ['clients'], [{'name': 'Кухни', 'price': 12.5, 'calls': 0, 'minutes': 0.0, 'cost': 0, 'charge': 0.0, 'profit': 0.0}])
        a = await (await self.http.get('/api/robot/cab/analytics')).json()
        self.assertEqual((a['total'], a['reached'], a['leads'], a['dial_rate'], a['conversion']), (3, 2, 1, 66.7, 33.3))
        self.assertEqual(a['cost'], 27.3)  # 2 мин × 1,5 + 2000 симв. × 10/1000 + 3000 ток. × 1/1000 + 8 × 0,1626 распознавание
        self.assertEqual(a['lead_cost'], 27.3)
        self.assertEqual({e['key']: e['count'] for e in a['ends']}['do_not_call'], 1)

    async def test_dial_failures_keep_or_close_numbers(self):
        """Сбой Asterisk номер не теряет; плохой номер закрывается; потерянный итог последней попытки не крутит номер по кругу."""
        await self.as_director()
        await self.http.post('/api/robot/cab/contacts/upload', json={'name': 'b.csv', 'text': '89990000021\n89990000022\n'}, headers=H)
        r = await self.http.post('/api/robot/cab/campaigns', json={'name': 'Сбои', 'operator': 'kitchen', 'days': [1, 2, 3, 4, 5, 6, 7],
                                                                     'start_time': '00:00', 'end_time': '23:59', 'attempts': 1,
                                                                     'contacts': 'all'}, headers=H)
        camp = await r.json()
        await self.http.post(f"/api/robot/cab/campaigns/{camp['id']}/start", json={}, headers=H)
        targets = lambda: self.pool.fetch('SELECT phone, done, outcome, attempts FROM robot_targets ORDER BY id')  # noqa: E731
        now = datetime.now(timezone.utc)
        for status, error in ((503, 'All connection attempts failed'), (400, 'Asterisk ARI 500: internal')):
            self.dial_fail = (status, error)
            self.assertIsNone(await robot_runner.tick(self.pool, now))
            self.assertEqual([tuple(r) for r in await targets()][0], ('79990000021', False, None, 0))
            now += timedelta(minutes=robot_runner.DOWN_MIN)
        self.dial_fail = (400, 'номер слишком короткий')
        self.assertIsNone(await robot_runner.tick(self.pool, now))
        self.assertEqual([tuple(r) for r in await targets()][0], ('79990000021', True, 'error', 0))
        self.dial_fail = None
        self.assertEqual((await robot_runner.tick(self.pool, now))['phone'], '79990000022')
        later = now + timedelta(minutes=robot_runner.HOLD_MIN + 1)  # итог звонка потерялся, а попытка была последней
        self.assertIsNone(await robot_runner.tick(self.pool, later))
        self.assertEqual([tuple(r) for r in await targets()][1], ('79990000022', True, 'error', 1))
        self.assertEqual(await self.pool.fetchval('SELECT status FROM robot_campaigns WHERE id=$1', camp['id']), 'done')

    async def test_huge_ids_and_repeat_upload(self):
        """Номер больше int64 — 404/400, а не 500; повторная загрузка тех же номеров не считается добавлением."""
        await self.as_director()
        big = '9' * 26
        self.assertEqual((await self.http.get(f'/api/robot/cab/campaigns/{big}/targets')).status, 404)
        self.assertEqual((await self.http.post(f'/api/robot/cab/campaigns/{big}/pause', json={}, headers=H)).status, 404)
        r = await self.http.post('/api/robot/cab/contacts/delete', json={'ids': [int(big)]}, headers=H)
        self.assertEqual((r.status, (await r.json())['deleted']), (200, 0))
        for added in (2, 0):
            r = await self.http.post('/api/robot/cab/contacts/upload', json={'name': 'c.csv', 'text': '89990000031\n89990000032\n'}, headers=H)
            self.assertEqual(((await r.json())['added'], (await r.json())['total']), (added, 2))

    async def test_minute_cost_after_quick_replies(self):
        await robot_store.set_option(self.pool, 'prices_auto', {'mango_min': {'value': 1.5}, 'eleven_1k_chars': {'value': 10},
                                                                'llm_1k_tokens': {'value': 1}})
        now = datetime.now(timezone.utc)
        change = now - timedelta(days=2)

        async def add(call_id, at, seconds, tokens, chars):
            await self.pool.execute('INSERT INTO robot_calls(call_id, at, outcome, duration_s, llm_tokens, tts_chars) '
                                    "VALUES ($1, $2, 'callback', $3, $4, $5)", call_id, at, seconds, tokens, chars)

        await add('old', change - timedelta(days=1), 120, 3000, 2000)  # до заготовок: токенов втрое больше
        await add('new', now - timedelta(hours=1), 60, 500, 1000)
        with patch.object(robot_store, 'QUICK_SINCE', change):
            econ = await robot_store.economics(self.pool)
            # новых звонков меньше 10 минут: старые токены × 0,34 + новые
            self.assertEqual((econ['estimate'], econ['chars_min'], econ['tokens_min']), (True, 1000, 507))
            await add('new2', now - timedelta(minutes=30), 600, 1000, 6000)
            econ = await robot_store.economics(self.pool)
            # 11 минут новых звонков: считаем только по ним
            self.assertEqual((econ['estimate'], econ['chars_min'], econ['tokens_min']), (False, 636, 136))

    async def test_client_paid_option(self):
        token = await auth.invite_create(self.pool, 1, 'client', client_key='Кухни')
        await self.http.get('/p/' + token)
        st = await (await self.http.get('/api/robot/cab')).json()
        self.assertFalse(st['enabled'])
        self.assertEqual((await self.http.get('/api/robot/cab/calls')).status, 403)
        await robot_store.set_option(self.pool, 'clients', ['Кухни'])
        await robot_store.set_option(self.pool, 'prices_auto', {'mango_min': {'value': 1}})
        await robot_store.set_option(self.pool, 'prices', {'client_min': {'Кухни': 3}})
        self.call_line(call_id='c1', phone='79990000001', outcome='interested', duration_s=60, owner='Кухни')
        self.call_line(call_id='d1', phone='79990000002', outcome='interested', duration_s=60)
        await robot_runner.ingest(self.pool)
        st = await (await self.http.get('/api/robot/cab?owner=')).json()
        self.assertEqual((st['enabled'], st['owner'], st['owners']), (True, 'Кухни', []))
        self.assertNotIn('kitchen', [o['id'] for o in st['operators']])  # встроенные операторы — нашего кабинета
        calls = (await (await self.http.get('/api/robot/cab/calls')).json())['items']
        self.assertEqual([(c['id'], c['cost']) for c in calls], [('c1', 3.0)])  # только свои, минута по 3 ₽
        a = await (await self.http.get('/api/robot/cab/analytics')).json()
        self.assertEqual((a['cost'], a['lead_cost'], a['priced']), (3.0, 3.0, True))
        self.assertNotIn('client_cost', a)
        tabs = await (await self.http.get('/api/robot/cab/contacts/tabs')).json()
        self.assertEqual(tabs['tabs'], ['Кухни'])
        r = await self.http.post('/api/robot/cab/contacts/sheet', json={'tab': 'Кухни'}, headers=H)
        self.assertEqual((await r.json())['total'], 4)
        data = base64.b64encode(xlsx([['Телефон'], [79990005555]])).decode()
        r = await self.http.post('/api/robot/cab/contacts/upload', json={'name': 'база.xlsx', 'data': data}, headers=H)
        self.assertEqual((await r.json())['total'], 5)
        r = await self.http.post('/api/robot/cab/contacts/sheet', json={'tab': 'Окна'}, headers=H)
        self.assertEqual(r.status, 400)
        r = await self.http.post('/api/robot/cab/options', json={'clients': []}, headers=H)
        self.assertEqual(r.status, 403)
        r = await self.http.post('/api/robot/cab/operators', json={'copy_from': 'kitchen'}, headers=H)
        self.assertEqual(r.status, 403)  # чужой (наш) оператор клиенту не виден

    async def test_manager_and_test_client_cannot_write(self):
        await self.enter('manager', ext='101')
        self.assertEqual((await self.http.get('/api/robot/cab/calls')).status, 403)
        await self.http.get('/test-exit')
        await self.enter('director')
        r = await self.http.post('/api/robot/cab/stop', json={'phones': '89990000013'}, headers=H)
        self.assertEqual(r.status, 403)
