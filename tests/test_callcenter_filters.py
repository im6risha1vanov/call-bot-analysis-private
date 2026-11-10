"""Фильтры «Колл-центра»: период, сотрудник, сверка с Mango, робот и доступ. Данные вымышленные."""
import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
os.environ.setdefault('ENCRYPTION_KEY', 'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE=')

from crm import auth, cabinet, call_analysis, callcenter_access, callcenter_db, callcenter_match, callcenter_robot, gsheets
from tools import Actor

TODAY = date(2026, 10, 11)
TZ = ZoneInfo('Europe/Moscow')
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
SECOND_CLIENT = [HEAD_ROW,
                 ['9', 'ИТ', 'сайт-в', '79990000003', '', '', '', '', 'Перезвон', '', '', '10',
                  '10.10.2026', '11.10.2026', '', '']]
EMPLOYEES = [{'extension': '13', 'full_name': 'Робот', 'role': 'manager'},
             {'extension': '14', 'full_name': 'Ольга Просвирова', 'role': 'manager'},
             {'extension': '15', 'full_name': 'Оксана Кирнева', 'role': 'manager'}]


def call(number, extension, phone, day, hour=12, *, campaign=None):
    return {'id': number, 'extension': extension, 'phone': phone,
            'at': datetime(2026, 10, day, hour, tzinfo=TZ), 'campaign': campaign or ''}


async def fake_values(sheet, range_):
    if sheet not in gsheets.allowed():
        raise PermissionError('Эта таблица CRM недоступна.')
    if range_.startswith('Сводка'):
        return SUMMARY
    if range_.startswith("'Кухни'"):
        return CLIENT
    if range_.startswith("'ИТ'"):
        return SECOND_CLIENT
    raise LookupError('Такого листа нет.')


async def fake_values_with_operator(sheet, range_):
    if range_.startswith('Сводка'):
        return SUMMARY
    if range_.startswith("'Кухни'"):
        return OP_CLIENT
    raise LookupError('Такого листа нет.')


def specs():
    return call_analysis.operator_specs({}, EMPLOYEES)


def robot_spec(**kwargs):
    base = {'id': 'robot', 'name': 'Робот', 'kind': 'robot', 'extension': '13', 'configured': True,
            'source': 'код: ROBOT_EXTENSION / ROBOT_EXTENSIONS', 'note': ''}
    base.update(kwargs)
    return base


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
        self.assertEqual(result['counts']['total'], 2)
        self.assertEqual((result['counts']['called'], result['counts']['reached'], result['counts']['interested']),
                         (2, 1, 1))
        self.assertEqual(result['waiting'], 1)
        self.assertEqual(result['days'], [{'day': '2026-10-11', 'calls': 2, 'first': 1, 'repeat': 1, 'reached': 1,
                                           'interested': 1}])
        self.assertEqual(result['reached_first'], 0)

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


class MatchTests(unittest.TestCase):
    """Связь звонков Mango с попытками — только по подтверждённым признакам."""

    def test_phone_key_normalizes_all_sheet_forms(self):
        self.assertEqual(callcenter_match.phone_key('+7 (999) 000-00-01'), '9990000001')
        self.assertEqual(callcenter_match.phone_key('8 999 000 00 01'), '9990000001')
        self.assertEqual(callcenter_match.phone_key('79990000001'), '9990000001')
        self.assertEqual(callcenter_match.phone_key('7,98E+10'), '9800000000')
        self.assertEqual(callcenter_match.phone_key('123'), '')  # не телефон: связи не будет

    def test_mask_keeps_only_the_tail(self):
        self.assertEqual(callcenter_match.mask('79990000001'), '+7 *** ** 01')
        self.assertNotIn('9990000', callcenter_match.mask('79990000001'))

    def test_links_by_phone_and_day_only(self):
        records = cabinet.sheet_records(CLIENT)
        calls = [call(1, '15', '89990000001', 10), call(2, '14', '79990000001', 11)]
        report = callcenter_match.attribute([('Кухни', records)], calls, TZ)
        self.assertTrue(report['available'])
        self.assertEqual((report['linked'], report['ambiguous'], report['repeated'], report['unmatched']), (2, 0, 0, 0))
        self.assertEqual([a['by'] for a in records[0]['attempts']], ['15', '14', ''])
        self.assertEqual({k: v['linked'] for k, v in report['by_extension'].items()}, {'15': 1, '14': 1})

    def test_ambiguous_phone_and_day_is_not_linked(self):
        records = cabinet.sheet_records(CLIENT)
        other = cabinet.sheet_records(SECOND_CLIENT)
        calls = [call(1, '15', '79990000003', 11)]
        report = callcenter_match.attribute([('Кухни', records), ('ИТ', other)], calls, TZ)
        self.assertEqual((report['linked'], report['ambiguous']), (0, 1))
        self.assertEqual(records[2]['attempts'][0]['by'], '')  # связь не выдумана
        self.assertEqual(report['samples']['ambiguous'][0]['phone'], '+7 *** ** 03')

    def test_repeated_call_beyond_attempts_is_reported_separately(self):
        records = cabinet.sheet_records(CLIENT)
        calls = [call(1, '15', '79990000001', 10, 9), call(2, '15', '79990000001', 10, 15)]
        report = callcenter_match.attribute([('Кухни', records)], calls, TZ)
        self.assertEqual((report['linked'], report['repeated'], report['unmatched']), (1, 1, 0))
        self.assertEqual(report['samples']['repeated'][0]['note'], 'звонок сверх попыток, записанных в листе')

    def test_missing_fields_are_not_linked(self):
        rows = [HEAD_ROW,
                ['1', 'Кухни', 'сайт-а', '', '', '', '', '', 'НДЗ', '', '', '10', '10.10.2026', '10.10.2026', '', ''],
                ['2', 'Кухни', 'сайт-а', '79990000005', '', '', '', '', 'НДЗ', '', '', '10',
                 '10.10.2026', '10.10.2026', '', '']]
        records = cabinet.sheet_records(rows)
        report = callcenter_match.attribute([('Кухни', records)], [call(1, '15', '79990000005', 10)], TZ)
        self.assertEqual(report['missing'], {'phone': 1, 'date': 0})
        self.assertEqual((report['linked'], report['unmatched']), (1, 0))

    def test_attempt_without_date_is_not_linked(self):
        rows = [HEAD_ROW,
                ['2', 'Кухни', 'сайт-а', '79990000005', '', '', '', '', 'НДЗ', '', '', '10',
                 '10.10.2026', '', '', '']]
        records = cabinet.sheet_records(rows)
        report = callcenter_match.attribute([('Кухни', records)], [call(1, '15', '79990000005', 10)], TZ)
        self.assertEqual(report['missing'], {'phone': 0, 'date': 1})
        self.assertEqual((report['linked'], report['unmatched']), (0, 1))

    def test_call_without_lead_is_unmatched_and_not_a_lead(self):
        records = cabinet.sheet_records(CLIENT)
        report = callcenter_match.attribute([('Кухни', records)], [call(1, '15', '79990009999', 10)], TZ)
        self.assertEqual((report['linked'], report['unmatched']), (0, 1))
        self.assertEqual(report['by_extension']['15']['unmatched'], 1)

    def test_campaign_mismatch_is_not_linked(self):
        records = cabinet.sheet_records(CLIENT)
        report = callcenter_match.attribute([('Кухни', records)],
                                            [call(1, '15', '79990000001', 10, campaign='Другая кампания')], TZ)
        self.assertEqual((report['linked'], report['campaign_mismatch']), (0, 1))

    def test_truncated_journal_does_not_claim_attempts_without_call(self):
        records = cabinet.sheet_records(CLIENT)
        report = callcenter_match.attribute([('Кухни', records)], [], TZ, truncated=True)
        self.assertIsNone(report['attempts_without_call'])
        self.assertEqual(report['linked'], 0)


class SheetCallcenterTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, **kwargs):
        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads'}), patch.object(gsheets, 'values', fake_values):
            return await cabinet.callcenter('leads', today=TODAY, tz=TZ, **kwargs)

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

    async def test_operator_filter_without_mango_calls_is_refused(self):
        with self.assertRaises(ValueError) as err:
            await self.call(operator='olga', operators=specs())
        self.assertIn('нет колонки оператора', str(err.exception))

    async def test_matching_makes_operator_slice_work(self):
        calls = {'items': [call(1, '15', '79990000001', 10), call(2, '14', '79990000001', 11),
                           call(3, '15', '79990000002', 10)], 'truncated': False}
        cc = await self.call(operator='oksana', operators=specs(), calls=calls)
        f = cc['filters']
        self.assertTrue(f['operator_available'])
        self.assertTrue(f['matching']['available'])
        self.assertEqual((f['matching']['linked'], f['matching']['unmatched']), (3, 0))
        oksana = next(o for o in f['operators'] if o['id'] == 'oksana')
        self.assertEqual(oksana['attempts'], 2)
        self.assertEqual(cc['clients'][0]['leads'], 2)
        self.assertIsNone(cc['total']['sum'])  # сумма листа по-прежнему не делится

    async def test_operator_column_makes_the_filter_work(self):
        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads'}), \
                patch.object(gsheets, 'values', fake_values_with_operator):
            cc = await cabinet.callcenter('leads', operator='oksana', operators=specs(), today=TODAY, tz=TZ)
        self.assertTrue(cc['filters']['operator_available'])
        self.assertEqual(cc['filters']['operator'], 'oksana')
        self.assertEqual([(o['id'], o['available']) for o in cc['filters']['operators']],
                         [('olga', True), ('oksana', True), ('robot', True)])  # робот есть в колонке листа
        self.assertEqual(cc['clients'][0]['leads'], 2)

    async def test_robot_slice_is_passed_through_and_not_mixed(self):
        robot = {'available': True, 'reason': '', 'source': 'Вкладка «Робот»', 'cabinet': 'наш кабинет',
                 'attempts': 4, 'leads': 1, 'phones': 3, 'phones_without_lead': 2, 'outcomes': [], 'note': 'n'}
        cc = await self.call(period='today', operators=specs(), robot=robot, operator='robot')
        f = cc['filters']
        self.assertEqual(f['robot']['attempts'], 4)
        self.assertEqual(f['robot']['leads'], 1)
        robot_state = next(o for o in f['operators'] if o['id'] == 'robot')
        self.assertTrue(robot_state['available'])
        self.assertEqual(robot_state['robot']['attempts'], 4)
        self.assertEqual(robot_state['attempts'], 0)  # попытки листа роботу не приписаны
        self.assertEqual(cc['total']['called'], 0)  # срез листа по роботу пуст: его числа отдельно
        plain = await self.call(period='today', operators=specs(), robot=robot)
        self.assertEqual(plain['total']['called'], 2)  # без выбора робота числа листа не меняются


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
            return await cabinet.callcenter(callcenter_db.DbSource(self.pool(), 1), today=TODAY, tz=TZ, **kwargs)

    async def test_period_and_operator_slice_with_prices(self):
        cc = await self.cc(period='today', operators=specs())
        self.assertEqual(cc['mode'], 'db')
        self.assertTrue(cc['filters']['operator_available'])
        self.assertEqual(cc['total']['called'], 2)
        self.assertEqual(cc['total']['sum'], 160)
        self.assertEqual([(o['id'], o['attempts']) for o in cc['filters']['operators']],
                         [('olga', 0), ('oksana', 1), ('robot', 0)])
        self.assertEqual(cc['filters']['unattributed'], 1)
        self.assertFalse(cc['filters']['matching']['available'])  # в базе сверка не нужна

    async def test_operator_only_period(self):
        cc = await self.cc(operators=specs(), operator='olga')
        self.assertEqual(cc['clients'][0]['leads'], 1)
        self.assertEqual(cc['total']['sum'], 10)
        self.assertEqual(cc['total']['called'], 1)

    async def test_unknown_operator(self):
        with self.assertRaises(ValueError):
            await self.cc(operators=specs(), operator='petya')

    async def test_sum_without_filter_is_unchanged(self):
        cc = await self.cc()
        self.assertEqual(cc['total']['sum'], 180)
        self.assertTrue(cc['filters']['sum_available'])

    async def test_robot_block_does_not_replace_db_attempts(self):
        robot = {'available': True, 'reason': '', 'source': 'Вкладка «Робот»', 'cabinet': 'наш кабинет',
                 'attempts': 7, 'leads': 2, 'phones': 5, 'phones_without_lead': 3, 'outcomes': [], 'note': 'n'}
        cc = await self.cc(period='today', operators=specs(), operator='robot', robot=robot)
        self.assertEqual(cc['filters']['robot']['attempts'], 7)
        self.assertEqual(next(o for o in cc['filters']['operators'] if o['id'] == 'robot')['attempts'], 0)
        self.assertEqual(cc['total']['called'], 0)  # срез листа по роботу пуст: его звонки не подменяют лист
        plain = await self.cc(period='today', operators=specs(), robot=robot)
        self.assertEqual(plain['total']['called'], 2)  # без выбора робота числа листа не меняются


class RobotPool:
    """Вкладка «Робот»: robot_calls с меткой кабинета и подтверждённые лиды CRM."""

    def __init__(self, calls, leads=(), tables=('robot_calls', 'crm_leads')):
        self.calls, self.leads, self.tables = calls, list(leads), set(tables)

    async def fetchval(self, query, *args):
        if 'to_regclass' in query:
            return args[0] in self.tables
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if 'FROM crm_leads' in query:
            return self.leads
        if 'FROM robot_calls' in query:
            owner, start, end = args
            return [c for c in self.calls if c['owner'] == owner
                    and (start is None or c['at'] >= start) and (end is None or c['at'] < end)]
        raise AssertionError(query)


def robot_call(call_id, phone, at, owner='', outcome='interested'):
    return {'call_id': call_id, 'owner': owner, 'phone': phone, 'at': at, 'outcome': outcome, 'duration_s': 30,
            'operator': 'kitchen', 'campaign_id': None}


class RobotTests(unittest.IsolatedAsyncioTestCase):
    CALLS = [robot_call('r1', '79990000001', datetime(2026, 10, 10, 12, tzinfo=TZ)),
             robot_call('r2', '79990000005', datetime(2026, 10, 10, 13, tzinfo=TZ), outcome='callback'),
             robot_call('r3', '79990000001', datetime(2026, 9, 1, 12, tzinfo=TZ)),
             robot_call('r4', '79990000006', datetime(2026, 10, 10, 12, tzinfo=TZ), owner='#2')]
    LEADS = [{'id': 1, 'number': '79990000001', 'status': 'interested'},
             {'id': 2, 'number': '79990000005', 'status': 'new'}]

    async def run_slice(self, pool, operator=None):
        with patch.dict(os.environ, {'CRM_HOME_COMPANY': '1'}):
            return await callcenter_robot.slice(pool, 1, date(2026, 10, 10), date(2026, 10, 10), TZ,
                                                operator or robot_spec())

    async def test_company_and_period_are_respected(self):
        answer = await self.run_slice(RobotPool(self.CALLS, self.LEADS))
        self.assertTrue(answer['available'])
        self.assertEqual((answer['attempts'], answer['leads']), (2, 1))  # r3 вне периода, r4 чужая компания
        self.assertEqual(answer['phones'], 2)
        self.assertEqual(answer['phones_without_lead'], 1)
        self.assertEqual(answer['cabinet'], 'наш кабинет')
        self.assertEqual((answer['start'], answer['end']), ('2026-10-10', '2026-10-10'))

    async def test_robot_outcomes_are_not_leads(self):
        answer = await self.run_slice(RobotPool(self.CALLS, []))
        self.assertEqual(answer['leads'], 0)  # собственный итог робота лидом не считается
        self.assertEqual({o['key'] for o in answer['outcomes']}, {'interested', 'callback'})

    async def test_missing_robot_table_is_reported(self):
        answer = await self.run_slice(RobotPool([], tables=('crm_leads',)))
        self.assertFalse(answer['available'])
        self.assertIn('robot_calls', answer['reason'])

    async def test_unconfigured_robot_is_reported(self):
        answer = await self.run_slice(RobotPool([]), robot_spec(configured=False, extension=None,
                                                              note='Добавочный робота не подтверждён.'))
        self.assertFalse(answer['available'])
        self.assertIn('не подтверждён', answer['reason'])


class RowsPool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query, client_id=1):
        return self.rows


PEOPLE = [
    {'extension': 'head', 'full_name': 'Григорий Иванов', 'role': 'owner', 'telegram_user_id': 901,
     'telegram_username': 'grisha'},
    {'extension': 'head:evgeniy', 'full_name': 'Евгений Петров', 'role': 'head', 'telegram_user_id': 902,
     'telegram_username': 'evgeny'},
    {'extension': 'head:petr', 'full_name': 'Пётр Сидоров', 'role': 'head', 'telegram_user_id': 903,
     'telegram_username': 'pivondopola'},
]


def viewer(actor, role='director', test=False, support=False):
    return auth.Viewer(role, actor, None, test, 1, support=support)


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, actor, rows=PEOPLE, config='', **kwargs):
        return await callcenter_access.check(RowsPool(rows), viewer(actor, **kwargs), config=config)

    async def test_verified_accounts_have_access(self):
        rows = [dict(row) for row in PEOPLE]
        for row, identity in zip(rows, (841868875, 304327068, 562570177)):
            row['telegram_user_id'] = identity
        for row in rows:
            actor = Actor(row['telegram_user_id'], 1, row['role'], row['extension'])
            self.assertTrue((await self.check(actor, rows=rows))['allowed'])

    async def test_name_alone_does_not_grant_access(self):
        status = await self.check(Actor(901, 1, 'owner', None))
        self.assertFalse(status['allowed'])
        grigory = next(t for t in status['targets'] if t['key'] == 'grigory')
        self.assertFalse(grigory['confirmed'])
        self.assertIn('Telegram ID', grigory['reason'])
        self.assertIn('CALLCENTER_FILTER_VIEWERS', status['reason'])

    async def test_confirmed_id_grants_pivondopola(self):
        rows = [dict(row) for row in PEOPLE]
        rows[2]['telegram_user_id'] = 562570177
        status = await self.check(Actor(562570177, 1, 'head', 'head:petr'), rows=rows)
        self.assertTrue(status['allowed'])
        self.assertFalse(status['override'])
        last = next(t for t in status['targets'] if t['key'] == 'pivondopola')
        self.assertTrue(last['confirmed'])
        self.assertEqual(last['via'], 'tg')

    async def test_configured_identifiers_are_verifiable(self):
        config = 'grigory=tg:901; evgeny=ext:head:evgeniy'
        status = await self.check(Actor(901, 1, 'owner', None), config=config)
        self.assertTrue(status['allowed'])
        self.assertTrue(status['override'])
        self.assertEqual(next(t for t in status['targets'] if t['key'] == 'grigory')['via'], 'tg')
        self.assertTrue((await self.check(Actor(902, 1, 'head', 'head:evgeniy'), config=config))['allowed'])
        self.assertTrue((await self.check(Actor(901, 1, 'owner', None),
                                          config='grigory=username:grisha'))['allowed'])

    async def test_other_director_is_refused(self):
        status = await self.check(Actor(555, 1, 'head', 'head:other'))
        self.assertFalse(status['allowed'])
        self.assertIn('Григорий, Евгений и pivondopola', status['reason'])
        self.assertFalse((await self.check(Actor(123, 1, 'manager', '101'), role='manager'))['allowed'])

    async def test_test_and_support_entries_are_refused(self):
        self.assertFalse((await self.check(Actor(901, 1, 'owner', None), test=True))['allowed'])
        self.assertFalse((await self.check(Actor(901, 1, 'owner', None), support=True))['allowed'])

    async def test_ambiguous_identifier_is_refused(self):
        rows = PEOPLE + [{'extension': 'head:second', 'full_name': 'Григорий Второй', 'role': 'head',
                          'telegram_user_id': 901, 'telegram_username': 'grisha2'}]
        status = await self.check(Actor(901, 1, 'owner', None), rows=rows, config='grigory=tg:901')
        self.assertFalse(status['allowed'])
        grigory = next(t for t in status['targets'] if t['key'] == 'grigory')
        self.assertIn('несколько записей', grigory['reason'])

    async def test_one_row_cannot_confirm_two_targets(self):
        status = await self.check(Actor(901, 1, 'owner', None), config='grigory=tg:901; evgeny=tg:901')
        self.assertFalse(status['allowed'])
        self.assertIn('нескольким адресатам', status['reason'])

    async def test_config_problems_are_reported_without_values(self):
        status = await self.check(Actor(901, 1, 'owner', None), config='грigory=901')
        self.assertFalse(status['allowed'])
        self.assertTrue(status['problems'])

    async def test_unlinked_session_is_refused(self):
        status = await callcenter_access.check(RowsPool([]), viewer(Actor(0, 1, 'owner', None)), config='')
        self.assertFalse(status['allowed'])
        self.assertIn('не привязан', status['reason'])


if __name__ == '__main__':
    unittest.main()
