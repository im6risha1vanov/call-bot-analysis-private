"""Фильтры «Колл-центра»: период, сотрудник и доступ. Данные вымышленные."""
import os
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
os.environ.setdefault('ENCRYPTION_KEY', 'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE=')

from crm import auth, cabinet, call_analysis, callcenter_access, callcenter_db, gsheets
from tools import Actor

TODAY = date(2026, 10, 11)
HEAD_ROW = ['id', 'Проект', 'Тег проекта', 'Телефон', 'Создано', 'Напоминание', 'Заметка', 'Комментарий',
            'Звонок 1 (первичный)', 'Звонок 2 (повторный)', 'Звонок 3 (повторный)', 'Сумма', 'Дата базы',
            'Дата звонка 1', 'Дата звонка 2', 'Дата звонка 3']
SUMMARY = [['Тарифы'], ['Вид звонка', 'Цена'], ['Первичный звонок', '10 ₽'], ['Повторный звонок (после НДЗ)', '5 ₽'],
           ['Звонок с результатом «Заинтересованный»', '150 ₽'], [], ['Клиент (название листа)', 'Первичные',
           'Повторные', 'Заинтересованные', 'Сумма'], ['Кухни', '3', '2', '1', '190 ₽'], ['ИТОГО', '3']]
CLIENT = [HEAD_ROW,
          ['1', 'Кухни', 'сайт-а', '79990000001', '', '', '', 'хочет замер', 'НДЗ', 'Заинтересованный', '', '165',
           '10.10.2026', '10.10.2026', '11.10.2026', ''],
          ['2', 'Кухни', 'сайт-б', '79990000002', '', 'пт после 12', '', '', 'Перезвон', '', '', '10',
           '10.10.2026', '10.10.2026', '', ''],
          ['3', 'Кухни', 'сайт-а', '79990000003', '', '', '', '', 'НДЗ', 'НДЗ 2', '', '15',
           '11.10.2026', '11.10.2026', '12.10.2026', ''],
          ['4', 'Кухни', '', '79990000004', '', '', '', '', '', '', '', '', '12.10.2026', '', '', ''],
          ['', 'итог']]
OP_CLIENT = [HEAD_ROW + ['Оператор'],
             ['1', 'Кухни', 'сайт-а', '79990000001', '', '', '', '', 'НДЗ', 'Заинтересованный', '', '165',
              '10.10.2026', '10.10.2026', '11.10.2026', '', 'Ольга'],
             ['2', 'Кухни', 'сайт-б', '79990000002', '', '', '', '', 'Перезвон', '', '', '10',
              '10.10.2026', '10.10.2026', '', '', 'Оксана'],
             ['3', 'Кухни', 'сайт-а', '79990000003', '', '', '', '', 'НДЗ', 'НДЗ 2', '', '15',
              '11.10.2026', '11.10.2026', '12.10.2026', '', 'Оксана']]
EMPLOYEES = [{'extension': '13', 'full_name': 'Робот', 'role': 'manager'},
             {'extension': '14', 'full_name': 'Ольга Просвирова', 'role': 'manager'},
             {'extension': '15', 'full_name': 'Оксана Кирнева', 'role': 'manager'}]


async def fake_values(sheet, range_):
    if sheet not in gsheets.allowed():
        raise PermissionError('Эта таблица CRM недоступна.')
    if range_.startswith('Сводка'):
        return SUMMARY
    if range_.startswith("'Кухни'"):
        return CLIENT
    raise LookupError('Такого листа нет.')


async def fake_values_with_operator(sheet, range_):
    if range_.startswith('Сводка'):
        return SUMMARY
    if range_.startswith("'Кухни'"):
        return OP_CLIENT
    raise LookupError('Такого листа нет.')


def specs():
    return call_analysis.operator_specs({}, EMPLOYEES)


class RecordsTests(unittest.TestCase):
    def test_period_range(self):
        self.assertEqual(cabinet.period_range('today', TODAY), (TODAY, TODAY))
        self.assertEqual(cabinet.period_range('7', TODAY), (date(2026, 10, 5), TODAY))
        self.assertEqual(cabinet.period_range('month', TODAY), (date(2026, 10, 1), TODAY))
        with self.assertRaises(ValueError):
            cabinet.period_range('год', TODAY)

    def test_sheet_without_operator_column_has_no_operator(self):
        self.assertEqual(cabinet.sheet_operator_column(CLIENT[:1]), -1)
        result = cabinet.parse_records(cabinet.sheet_records(CLIENT), keep=lambda a: True)
        self.assertIsNone(result['cost'])  # сумма листа не делится по звонкам
        self.assertEqual(result['counts']['total'], 4)

    def test_period_keeps_calls_of_the_period(self):
        records = cabinet.sheet_records(CLIENT)
        result = cabinet.parse_records(records, keep=cabinet.keep_attempts(TODAY, TODAY, None))
        self.assertEqual(result['counts']['total'], 2)  # звонки 11.10: лиды 1 и 3
        self.assertEqual((result['counts']['called'], result['counts']['reached'], result['counts']['interested']),
                         (2, 1, 1))
        self.assertEqual(result['waiting'], 1)
        self.assertEqual(result['days'], [{'day': '2026-10-11', 'calls': 2, 'first': 1, 'repeat': 1, 'reached': 1,
                                           'interested': 1}])
        self.assertEqual(result['reached_first'], 0)  # первая попытка лида 1 была 10.10 и в срез не попала

    def test_operator_column_is_read_when_owner_added_it(self):
        self.assertEqual(cabinet.sheet_operator_column(OP_CLIENT), 16)
        records = cabinet.sheet_records(OP_CLIENT)
        self.assertEqual([a['by'] for a in records[0]['attempts']], ['Ольга', 'Ольга', 'Ольга'])
        oksana = {'id': 'oksana', 'name': 'Оксана Кирнева', 'short': 'Оксана', 'extension': '15'}
        result = cabinet.parse_records(records, keep=cabinet.keep_attempts(None, None, oksana))
        self.assertEqual(result['counts']['total'], 2)
        self.assertEqual(result['counts']['interested'], 0)

    def test_attempt_price_matches_tariffs(self):
        prices = {'first': 10, 'repeat': 5, 'interested': 150}
        self.assertEqual([cabinet.attempt_price(result, i, prices) for i, result in
                          enumerate(['НДЗ', 'Заинтересованный', ''])], [10, 150, 5])


class SheetCallcenterTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, **kwargs):
        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads'}), patch.object(gsheets, 'values', fake_values):
            return await cabinet.callcenter('leads', today=TODAY, **kwargs)

    async def test_unfiltered_answer_is_the_old_one(self):
        cc = await self.call()
        self.assertEqual((cc['total']['called'], cc['total']['reached'], cc['total']['reached_first']), (3, 2, 1))
        self.assertEqual(cc['clients'][0]['sum'], 190)
        self.assertTrue(cc['filters']['sum_available'])
        self.assertFalse(cc['filters']['operator_available'])
        self.assertIn('нет колонки оператора', cc['filters']['operator_reason'])

    async def test_period_without_operator_support(self):
        cc = await self.call(period='today')
        self.assertEqual(cc['filters']['period'], 'today')
        self.assertEqual(cc['filters']['period_label'], 'сегодня')
        self.assertEqual((cc['filters']['start'], cc['filters']['end']), ('2026-10-11', '2026-10-11'))
        self.assertEqual(cc['total']['called'], 2)
        self.assertIsNone(cc['total']['sum'])
        self.assertIsNone(cc['clients'][0]['sum'])
        self.assertFalse(cc['filters']['sum_available'])
        self.assertIn('по лиду целиком', cc['filters']['sum_reason'])

    async def test_operator_filter_refuses_google_sheet(self):
        with self.assertRaises(ValueError) as err:
            await self.call(operator='olga', operators=specs())
        self.assertIn('нет колонки оператора', str(err.exception))

    async def test_operator_column_makes_the_filter_work(self):
        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads'}), \
                patch.object(gsheets, 'values', fake_values_with_operator):
            cc = await cabinet.callcenter('leads', operator='oksana', operators=specs(), today=TODAY)
        self.assertTrue(cc['filters']['operator_available'])
        self.assertEqual(cc['filters']['operator'], 'oksana')
        self.assertEqual([(o['id'], o['available']) for o in cc['filters']['operators']],
                         [('olga', True), ('oksana', True), ('robot', True)])
        self.assertEqual(cc['clients'][0]['leads'], 2)


class FakePool:
    """Достаточно запросов колл-центра в базе: клиенты и их номера."""

    def __init__(self, clients, leads):
        self.clients, self.leads = clients, leads

    async def fetch(self, query, *args):
        if 'FROM cc_clients' in query and 'ORDER BY id' in query:
            return self.clients
        if 'attempts FROM cc_leads' in query:
            return [x for x in self.leads if x['cc_client_id'] == args[0]]
        if 'JOIN cc_clients c' in query:
            return [x for x in self.leads if x['client_id'] == args[0] and x['client'] == args[1]]
        raise AssertionError(query)


PRICES = {'first': 10.0, 'repeat': 5.0, 'interested': 150.0}


def lead(number, attempts, base='2026-10-10'):
    return {'id': number, 'client_id': 1, 'cc_client_id': 1, 'client': 'Кухни', 'phone': '7999000' + number,
            'tag': 'сайт', 'note': '', 'comment': '', 'remind': '', 'base_date': date.fromisoformat(base),
            'cc_leads_id': None, 'attempts': attempts}


LEADS = [
    lead('1', [{'r': 'НДЗ', 'd': '2026-10-10', 'by': '14'}, {'r': 'Заинтересованный', 'd': '2026-10-11', 'by': '15'}]),
    lead('2', [{'r': 'Перезвон', 'd': '2026-10-10', 'by': '15'}]),
    lead('3', [{'r': 'НДЗ', 'd': '2026-10-11', 'by': ''}]),
]
CLIENTS = [{'id': 1, 'name': 'Кухни'}]


class DBCallcenterTests(unittest.IsolatedAsyncioTestCase):
    def pool(self):
        return FakePool(CLIENTS, LEADS)

    async def cc(self, **kwargs):
        with patch.object(callcenter_db, 'prices', AsyncMock(return_value=dict(PRICES))):
            return await cabinet.callcenter(callcenter_db.DbSource(self.pool(), 1), today=TODAY, **kwargs)

    async def test_period_and_operator_slice_with_prices(self):
        cc = await self.cc(period='today', operators=specs())
        self.assertEqual(cc['mode'], 'db')
        self.assertTrue(cc['filters']['operator_available'])
        self.assertEqual(cc['total']['called'], 2)
        self.assertEqual(cc['total']['sum'], 160)  # 150 заинтересованный + 10 первичный без оператора
        self.assertEqual([(o['id'], o['attempts']) for o in cc['filters']['operators']],
                         [('olga', 0), ('oksana', 1), ('robot', 0)])
        self.assertEqual(cc['filters']['unattributed'], 1)  # попытка без добавочного

    async def test_operator_only_period(self):
        cc = await self.cc(operators=specs(), operator='olga')
        self.assertEqual(cc['clients'][0]['leads'], 1)
        self.assertEqual(cc['total']['sum'], 10)  # только первичный звонок Ольги
        self.assertEqual(cc['total']['called'], 1)

    async def test_unknown_operator(self):
        with self.assertRaises(ValueError):
            await self.cc(operators=specs(), operator='petya')

    async def test_sum_without_filter_is_unchanged(self):
        cc = await self.cc()
        self.assertEqual(cc['total']['sum'], 180)  # 10 + 150 + 10 + 10 по ценам компании
        self.assertTrue(cc['filters']['sum_available'])


class RowsPool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query, client_id=1):
        return self.rows


PEOPLE = [
    {'extension': 'head', 'full_name': 'Григорий Иванов', 'role': 'owner', 'telegram_user_id': 901,
     'telegram_username': 'grisha'},
    {'extension': 'head:evgeniy', 'full_name': 'Евгений Петров', 'role': 'head', 'telegram_user_id': 902,
     'telegram_username': 'evgeniy'},
    {'extension': 'head:petr', 'full_name': 'Пётр Сидоров', 'role': 'head', 'telegram_user_id': 903,
     'telegram_username': 'pivondopola'},
]


def viewer(actor, role='director', test=False, support=False):
    return auth.Viewer(role, actor, None, test, 1, support=support)


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, actor, rows=PEOPLE, **kwargs):
        return await callcenter_access.check(RowsPool(rows), viewer(actor, **kwargs), env={})

    async def test_three_confirmed_users(self):
        status = await self.check(Actor(901, 1, 'owner', None))
        self.assertTrue(status['allowed'])
        self.assertEqual([t['key'] for t in status['targets']], ['grigory', 'evgeny', 'pivondopola'])
        self.assertTrue(all(t['confirmed'] for t in status['targets']))
        self.assertTrue((await self.check(Actor(902, 1, 'head', 'head:evgeniy')))['allowed'])
        self.assertTrue((await self.check(Actor(903, 1, 'head', 'head:petr')))['allowed'])

    async def test_other_director_is_refused(self):
        status = await self.check(Actor(555, 1, 'head', 'head:other'))
        self.assertFalse(status['allowed'])
        self.assertIn('Григорий, Евгений и pivondopola', status['reason'])
        self.assertFalse((await self.check(Actor(123, 1, 'manager', '101'), role='manager'))['allowed'])

    async def test_test_and_support_entries_are_refused(self):
        self.assertFalse((await self.check(Actor(901, 1, 'owner', None), test=True))['allowed'])
        self.assertFalse((await self.check(Actor(901, 1, 'owner', None), support=True))['allowed'])

    async def test_ambiguous_name_is_not_guessed(self):
        rows = PEOPLE + [{'extension': 'head:grisha2', 'full_name': 'Григорий Второй', 'role': 'head',
                          'telegram_user_id': 904, 'telegram_username': 'grisha2'}]
        status = await self.check(Actor(901, 1, 'owner', None), rows=rows)
        self.assertFalse(status['allowed'])
        grigory = next(t for t in status['targets'] if t['key'] == 'grigory')
        self.assertFalse(grigory['confirmed'])
        self.assertIn('Подходит несколько', grigory['reason'])
        self.assertIn('Подходит несколько', status['reason'])

    async def test_missing_username_is_reported(self):
        rows = [p for p in PEOPLE if p['telegram_username'] != 'pivondopola']
        status = await self.check(Actor(903, 1, 'head', 'head:petr'), rows=rows)
        self.assertFalse(status['allowed'])
        last = next(t for t in status['targets'] if t['key'] == 'pivondopola')
        self.assertFalse(last['confirmed'])
        self.assertIn('ником pivondopola', last['reason'])

    async def test_env_override_confirms_access(self):
        rows = PEOPLE + [{'extension': 'head:extra', 'full_name': 'Новый Руководитель', 'role': 'head',
                          'telegram_user_id': 905, 'telegram_username': 'extra'}]
        status = await callcenter_access.check(RowsPool(rows), viewer(Actor(905, 1, 'head', 'head:extra')),
                                               env={'CALLCENTER_FILTER_VIEWERS': '905'})
        self.assertTrue(status['allowed'])
        self.assertTrue(status['override'])

    async def test_unlinked_session_is_refused(self):
        status = await callcenter_access.check(RowsPool([]), viewer(Actor(0, 1, 'owner', None)), env={})
        self.assertFalse(status['allowed'])
        self.assertIn('не привязан', status['reason'])


if __name__ == '__main__':
    unittest.main()
