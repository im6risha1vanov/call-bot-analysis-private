"""Конструктор автоматизаций: проверка сценария, шаблоны, очередь событий, повторы, лимиты, вебхуки.
Внешние сервисы подменены: ничего не уходит в Telegram, Google и чужие адреса."""
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
os.environ.setdefault('ENCRYPTION_KEY', 'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE=')

from crm import automation_blocks as blocks, automations, store
from crypto_util import encrypt
from tests.test_crm import DSN, DBCase

TG = {1: {'id': 1, 'kind': 'telegram', 'title': 'Telegram: Продажи', 'status': 'ready', 'error': None}}
PAID = {'trigger': {'type': 'deal.paid'}, 'if': [{'field': 'deal.amount', 'op': '>=', 'value': 50000}],
        'actions': [{'type': 'telegram.send', 'account': 1, 'text': 'Оплата {deal.amount} ₽ от {deal.client}, {deal.manager}'}]}


class PureTests(unittest.TestCase):
    def test_render_and_conditions(self):
        payload = {'deal': {'amount': 150000, 'client': 'ООО Ромашка', 'ok': True}}
        self.assertEqual(automations.render('{deal.amount} ₽, {deal.client}, {deal.ok}, {deal.nope}', payload),
                         '150 000 ₽, ООО Ромашка, да, ')
        self.assertTrue(automations.matches([{'field': 'deal.amount', 'op': '>=', 'value': '50 000'}], payload))
        self.assertFalse(automations.matches([{'field': 'deal.amount', 'op': '<', 'value': 1000}], payload))
        self.assertTrue(automations.matches([{'field': 'deal.client', 'op': 'contains', 'value': 'ромаш'}], payload))
        self.assertTrue(automations.matches([{'field': 'deal.note', 'op': 'empty'}], payload))
        self.assertFalse(automations.matches([{'field': 'deal.client', 'op': '>', 'value': 5}], payload))  # текст не число

    def test_validate_keeps_only_known_parts(self):
        clean = automations.validate({**PAID, 'extra': 1, 'actions': [{**PAID['actions'][0], 'code': 'import os'}]}, TG)
        self.assertEqual(clean, PAID)
        with self.assertRaisesRegex(ValueError, 'Неизвестное событие'):
            automations.validate({**PAID, 'trigger': {'type': 'deal.hacked'}}, TG)
        with self.assertRaisesRegex(ValueError, 'такого подключения нет'):
            automations.validate({**PAID, 'actions': [{**PAID['actions'][0], 'account': 2}]}, TG)
        with self.assertRaisesRegex(ValueError, 'ЧЧ:ММ'):
            automations.validate({**PAID, 'trigger': {'type': 'schedule.daily', 'time': '25:00'}}, TG)
        with self.assertRaisesRegex(ValueError, 'адресов'):
            automations.validate({**PAID, 'actions': [{'type': 'email.send', 'to': ['не адрес'], 'text': 'x'}]}, TG)
        with self.assertRaisesRegex(ValueError, 'https'):
            automations.validate({**PAID, 'actions': [{'type': 'webhook.send', 'url': 'file:///etc/passwd'}]}, TG)
        with self.assertRaisesRegex(ValueError, 'Такого действия нет'):
            automations.validate({**PAID, 'actions': [{'type': 'shell.run'}]}, TG)
        self.assertIn('Telegram: Продажи', automations.describe(PAID, TG))

    def test_telegram_code_and_sheet_id(self):
        self.assertEqual(automations.telegram_code('/start aut_abcdefABCDEF12'), 'abcdefABCDEF12')
        self.assertEqual(automations.telegram_code('/start@averon_bot aut_abcdefABCDEF12'), 'abcdefABCDEF12')
        self.assertIsNone(automations.telegram_code('/start inv_abcdefABCDEF12'))
        url = 'https://docs.google.com/spreadsheets/d/1chzMdgtuHf2ipvaVH6zEuY_aHAEolr15NqB9NMAZrKU/edit#gid=0'
        self.assertEqual(blocks.sheet_id(url), '1chzMdgtuHf2ipvaVH6zEuY_aHAEolr15NqB9NMAZrKU')
        self.assertIsNone(blocks.sheet_id('https://example.com'))

    def test_webhook_never_goes_inside(self):
        for host in ('127.0.0.1', 'localhost', '10.0.0.5', '169.254.169.254', '::1'):
            self.assertFalse(blocks.public_address(host), host)
        self.assertTrue(blocks.public_address('8.8.8.8'))


class FakeBlocks:
    """Подмена отправки: что ушло бы в Telegram и вебхук."""

    def __init__(self):
        self.sent = []
        self.fail_next = 0

    async def telegram(self, params, account, text):
        if self.fail_next:
            self.fail_next -= 1
            raise blocks.Retry('Telegram недоступен')
        self.sent.append((account['chat_id'], text))
        return text


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class EngineTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        root = Path(__file__).resolve().parents[1]
        await self.pool.execute((root / 'migrations/030_automations.sql').read_text())
        await self.pool.execute('TRUNCATE automation_events, automation_usage, automation_limits, automations, '
                                'automation_accounts CASCADE')
        self.acc = await self.pool.fetchval(
            "INSERT INTO automation_accounts(client_id, kind, title, data_enc, status) VALUES(1,'telegram','Telegram: Продажи',$1,'ready') RETURNING id",
            encrypt(json.dumps({'chat_id': -100500})))
        self.fake = FakeBlocks()
        patcher = patch.object(blocks, 'telegram_send', self.fake.telegram)
        patcher.start()
        self.addCleanup(patcher.stop)

    def spec(self, **over):
        return {**PAID, 'actions': [{**PAID['actions'][0], 'account': self.acc}], **over}

    async def make(self, spec=None, on=True):
        auto = await automations.save(self.pool, 1, None, 'Оплаты в чат', spec or self.spec(), 'Директор')
        if on:
            test = await automations.test_run(self.pool, 1, auto['id'])
            self.assertEqual(test['status'], 'ok')
            self.fake.sent.clear()
            await automations.set_enabled(self.pool, 1, auto['id'], True)
        return auto

    async def test_paid_deal_goes_to_telegram_once(self):
        await store.add_leads(self.pool, 1, '101', '89991234567; Иван; ООО Ромашка', 'ГЦК')
        auto = await self.make()
        await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, amount, extension) VALUES(1,'9991234567','paid',150000,'101')")
        await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, amount, extension) VALUES(1,'9991234567','paid',1000,'101')")
        await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, extension) VALUES(1,'9991234567','note','101')")
        await automations.tick(self.pool)
        await automations.tick(self.pool)  # повторный проход ничего не шлёт второй раз
        self.assertEqual(self.fake.sent, [(-100500, 'Оплата 150 000 ₽ от ООО Ромашка, Первый')])
        runs = await automations.runs(self.pool, 1, auto['id'])
        self.assertEqual([(r['status'], r['test']) for r in runs], [('ok', False), ('ok', True)])
        usage = (await automations.overview(self.pool, 1))['usage']
        self.assertEqual((usage['runs'], usage['active']), (1, 1))

    async def test_no_events_without_enabled_automations(self):
        await self.make(on=False)
        await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, amount) VALUES(1,'9991234567','paid',150000)")
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM automation_events'), 0)

    async def test_enable_needs_successful_test_and_edit_turns_off(self):
        auto = await automations.save(self.pool, 1, None, 'Оплаты', self.spec(), None)
        with self.assertRaisesRegex(ValueError, 'пробный запуск'):
            await automations.set_enabled(self.pool, 1, auto['id'], True)
        test = await automations.test_run(self.pool, 1, auto['id'])
        self.assertFalse(test['real_data'])  # сделок ещё нет: пример
        self.assertIn('Оплата 150', test['steps'][0]['preview'])
        self.assertTrue((await automations.set_enabled(self.pool, 1, auto['id'], True))['enabled'])
        edited = await automations.save(self.pool, 1, auto['id'], 'Оплаты', self.spec(**{'if': []}), None)
        self.assertFalse(edited['enabled'])
        with self.assertRaisesRegex(ValueError, 'Такого сценария нет'):
            await automations.save(self.pool, 2, auto['id'], 'Чужой',
                                   {**PAID, 'actions': [{'type': 'webhook.send', 'url': 'https://example.com/x'}]}, None)

    async def test_retry_then_success(self):
        auto = await self.make()
        self.fake.fail_next = 1
        await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, amount) VALUES(1,'9991234567','paid',90000)")
        await automations.tick(self.pool)
        run = (await automations.runs(self.pool, 1, auto['id']))[0]
        self.assertEqual(run['status'], 'retry')
        await self.pool.execute("UPDATE automation_runs SET retry_at=now() WHERE status='retry'")
        await automations.tick(self.pool)
        run = (await automations.runs(self.pool, 1, auto['id']))[0]
        self.assertEqual((run['status'], run['attempts']), ('ok', 2))
        self.assertEqual(len(self.fake.sent), 1)

    async def test_month_limit_and_loop_guard(self):
        auto = await self.make()
        await self.pool.execute('INSERT INTO automation_limits(client_id, max_runs_month) VALUES(1, 1)')
        for amount in (60000, 70000):
            await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, amount) VALUES(1,'9991234567','paid',$1)", amount)
        await automations.tick(self.pool)
        statuses = [r['status'] for r in await automations.runs(self.pool, 1, auto['id']) if not r['test']]
        self.assertEqual(sorted(statuses), ['limit', 'ok'])
        await self.pool.execute('DELETE FROM automation_limits')
        await self.pool.execute(
            "INSERT INTO automation_runs(automation_id, client_id, status) SELECT $1, 1, 'ok' FROM generate_series(1, $2)",
            auto['id'], automations.RATE_MAX)
        await self.pool.execute("INSERT INTO deal_events(client_id, phone, kind, amount) VALUES(1,'9991234567','paid',80000)")
        await automations.tick(self.pool)
        row = (await automations.overview(self.pool, 1))['automations'][0]
        self.assertFalse(row['enabled'])
        self.assertIn('зацикливание', row['paused_reason'])

    async def test_incoming_webhook_and_other_company(self):
        spec = {'trigger': {'type': 'webhook.in'}, 'if': [{'field': 'data.status', 'op': '=', 'value': 'won'}],
                'actions': [{'type': 'telegram.send', 'account': self.acc, 'text': 'Сделка в амо: {data.name}'}]}
        auto = await self.make(spec)
        token = auto['hook'].rsplit('/', 1)[1]
        self.assertTrue(await automations.incoming(self.pool, token, '{"status": "won", "name": "Окна"}'.encode()))
        self.assertTrue(await automations.incoming(self.pool, token, b'{"status": "lost"}'))
        self.assertFalse(await automations.incoming(self.pool, 'wrong', b'{}'))
        await automations.tick(self.pool)
        self.assertEqual(self.fake.sent, [(-100500, 'Сделка в амо: Окна')])
        with self.assertRaisesRegex(ValueError, 'такого подключения нет'):  # чужой аккаунт не подставить
            await automations.save(self.pool, 2, None, 'x', spec, None)

    async def test_daily_schedule_fires_once_per_day(self):
        spec = {'trigger': {'type': 'schedule.daily', 'time': '19:00'}, 'if': [],
                'actions': [{'type': 'telegram.send', 'account': self.acc, 'text': 'Итоги {date}: оплат {day.paid}'}]}
        auto = await self.make(spec)
        await self.pool.execute('UPDATE automations SET last_fired=NULL WHERE id=$1', auto['id'])
        before = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)  # 18:00 МСК
        after = datetime(2026, 10, 24, 16, 5, tzinfo=timezone.utc)   # 19:05 МСК
        await automations.schedule(self.pool, before)
        await automations.schedule(self.pool, after)
        await automations.schedule(self.pool, after)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM automation_events WHERE kind='schedule.daily'"), 1)
        await automations.tick(self.pool)
        self.assertEqual(self.fake.sent, [(-100500, 'Итоги 2026-10-24: оплат 0')])

    async def test_telegram_binding_and_account_in_use(self):
        with patch('crm.telegram.bot_username', AsyncMock(return_value='averon_bot')):
            link = await automations.telegram_link(self.pool, 1, 'Директор')
        code = link['private'].rsplit('aut_', 1)[1]
        self.assertTrue(link['group'].startswith('https://t.me/averon_bot?startgroup=aut_'))
        self.assertIn('подключён', await automations.bind_telegram(self.pool, code, -42, 'Продажи'))
        self.assertIn('устарела', await automations.bind_telegram(self.pool, code, -42, 'Продажи'))
        self.assertEqual(await automations.account_secret(self.pool, 1, link['id']), {'chat_id': -42})
        self.assertIsNone(await automations.account_secret(self.pool, 2, link['id']))  # чужая компания
        await self.make()
        with self.assertRaisesRegex(ValueError, 'используется'):
            await automations.delete_account(self.pool, 1, self.acc)

    async def test_manager_outcome_payload(self):
        await store.add_leads(self.pool, 1, '101', '89991234567; Иван; ООО Ромашка', 'ГЦК')
        spec = {'trigger': {'type': 'call.outcome'}, 'if': [{'field': 'call.outcome_code', 'op': '=', 'value': 'meeting'}],
                'actions': [{'type': 'telegram.send', 'account': self.acc, 'text': '{call.manager}: {call.outcome} с {call.client}'}]}
        await self.make(spec)
        await self.pool.execute("INSERT INTO crm_outcomes(client_id, extension, number, outcome) VALUES(1,'101','79991234567','meeting')")
        await self.pool.execute("INSERT INTO crm_outcomes(client_id, extension, number, outcome) VALUES(1,'101','79991234567','no_answer')")
        await automations.tick(self.pool)
        self.assertEqual(self.fake.sent, [(-100500, 'Первый: Назначил встречу с ООО Ромашка')])

    async def test_site_routes(self):
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web as crm_web
        auto = await self.make({'trigger': {'type': 'webhook.in'}, 'if': [],
                                'actions': [{'type': 'telegram.send', 'account': self.acc, 'text': '{data.x}'}]})
        async with TestClient(TestServer(crm_web.build(self.pool))) as http:
            r = await http.post(auto['hook'], json={'x': 'из другой системы'})  # без cookie и X-CRM
            self.assertEqual(r.status, 200)
            r = await http.post('/hook/nope', json={})
            self.assertEqual(r.status, 404)
            r = await http.get('/api/automations')
            self.assertEqual(r.status, 401)  # настройки только после входа
            r = await http.post('/api/automations', json={}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 401)
        await automations.tick(self.pool)
        self.assertEqual(self.fake.sent, [(-100500, 'из другой системы')])
