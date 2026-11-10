"""Проверки разбора звонков без производства: операторы, Москва, воронка, роли, Telegram."""
import os
import unittest
from types import SimpleNamespace

from crm import call_analysis as ca

FULL = """[00:00 Спикер 0] Здравствуйте, это Ольга, компания Averon, по поводу найма.
[00:05 Спикер 1] Да, слушаю.
[00:10 Спикер 0] Вы сейчас подбираете сотрудников? Сколько человек нужно?
[00:15 Спикер 1] Да, ищем менеджера по продажам, нужен один человек.
[00:20 Спикер 0] На hh.ru регистрация есть?
[00:25 Спикер 1] Нет регистрации, нет кабинета работодателя.
[00:30 Спикер 0] Специалист позвонит, договорились?
[00:35 Спикер 1] Да, договорились, пусть звонит."""

PARTIAL = """[00:00 Спикер 0] Здравствуйте, это Ольга, по поводу найма.
[00:05 Спикер 1] Да, слушаю.
[00:10 Спикер 0] Вы кого-то ищете?
[00:15 Спикер 1] Пока не знаю."""

ANSWER_MACHINE = "[00:00 Спикер 0] Здравствуйте, вы позвонили в компанию. Оставьте сообщение после сигнала."


def viewer(role='director', actor_role='owner', tg=555, test=False, support=False):
    actor = SimpleNamespace(role=actor_role, client_id=1, telegram_user_id=tg, extension=None)
    return SimpleNamespace(role=role, actor=actor, test=test, support=support, company=1)


class OperatorTests(unittest.TestCase):
    def test_confirmed_extensions_and_no_guessing_for_olga(self):
        specs = {x['id']: x for x in ca.operator_specs({}, [])}
        self.assertFalse(specs['olga']['configured'])
        self.assertEqual(specs['oksana']['extension'], '15')
        self.assertEqual(specs['robot']['extension'], '13')
        self.assertIn('CALL_ANALYSIS_OLGA_EXT', specs['olga']['note'])

    def test_olga_from_server_settings_or_unique_employee_only(self):
        specs = {x['id']: x for x in ca.operator_specs({'KITCHEN_EXTENSIONS': '14'}, [])}
        self.assertEqual(specs['olga']['extension'], '14')
        many = {x['id']: x for x in ca.operator_specs(
            {}, [{'extension': '14', 'full_name': 'Ольга Первая', 'role': 'manager'},
                 {'extension': '16', 'full_name': 'Ольга Вторая', 'role': 'manager'}])}
        self.assertFalse(many['olga']['configured'])
        one = {x['id']: x for x in ca.operator_specs(
            {}, [{'extension': '14', 'full_name': 'Ольга Просвирова', 'role': 'manager'}])}
        self.assertEqual(one['olga']['extension'], '14')

    def test_explicit_mapping_wins(self):
        env = {'CALL_ANALYSIS_OPERATORS': 'olga=18,oksana=19', 'KITCHEN_EXTENSIONS': '14'}
        specs = {x['id']: x for x in ca.operator_specs(env, [])}
        self.assertEqual((specs['olga']['extension'], specs['oksana']['extension']), ('18', '19'))
        json_env = {'CALL_ANALYSIS_OPERATORS': '{"olga":{"extension":"20"}}'}
        self.assertEqual(ca.operator_by_id(ca.operator_specs(json_env, []), 'olga')['extension'], '20')


class PeriodTests(unittest.TestCase):
    def test_moscow_boundaries_are_inclusive_days(self):
        start, end = ca.period_bounds('2026-11-01', '2026-11-02')
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ('2026-10-31T21:00:00+00:00', '2026-11-02T21:00:00+00:00'))
        self.assertEqual(ca.period_label('2026-11-01', '2026-11-02'), '01.11.2026–02.11.2026')

    def test_bad_period(self):
        with self.assertRaises(ValueError):
            ca.period_bounds('2026-11-03', '2026-11-02')
        with self.assertRaises(ValueError):
            ca.period_bounds('2026-01-01', '2026-03-01')

    def test_duplicate_key_is_stable_and_scoped(self):
        first = ca.dedup_key(1, 'olga', '14', '2026-11-01', '2026-11-02')
        self.assertEqual(first, ca.dedup_key(1, 'olga', '14', '2026-11-01', '2026-11-02'))
        self.assertNotEqual(first, ca.dedup_key(1, 'oksana', '15', '2026-11-01', '2026-11-02'))
        self.assertNotEqual(first, ca.dedup_key(2, 'olga', '14', '2026-11-01', '2026-11-02'))


class FunnelTests(unittest.TestCase):
    def test_full_funnel_is_detected_from_evidence(self):
        result = ca.classify_transcript(FULL, duration=40, confidence=0.92)
        self.assertEqual({k: v['state'] for k, v in result['stages'].items()}, {
            'attempt': 'yes', 'live_human': 'yes', 'qualification': 'yes', 'need': 'yes',
            'hh_criteria': 'yes', 'specialist': 'yes', 'crm_lead': 'unknown'})
        self.assertTrue(result['hiring_topic'])
        self.assertIn('нет регистрации', result['stages']['hh_criteria']['quote'].lower())

    def test_partial_transcript_does_not_invent_stages(self):
        result = ca.classify_transcript(PARTIAL, duration=20, confidence=0.9)
        self.assertEqual(result['stages']['live_human']['state'], 'yes')
        self.assertEqual(result['stages']['need']['state'], 'no')
        self.assertIn(result['stages']['hh_criteria']['state'], {'unknown', 'no'})

    def test_single_speaker_is_not_accused_of_operator_miss(self):
        result = ca.classify_transcript('Клиент ищет сотрудника, нужен менеджер, нет регистрации.', duration=30)
        self.assertTrue(result['speaker_uncertain'])
        reason = ca.reason_for({'status': 'done', 'phone': '79990000001', 'stages': result['stages'],
                                'speaker_uncertain': True, 'hiring_topic': True,
                                'low_confidence': False, 'scenario_hh_compatible': True})
        self.assertEqual(reason['code'], 'uncertain_transcript')
        self.assertEqual(reason['group'], 'technical')

    def test_empty_and_low_confidence_are_separate(self):
        empty = ca.classify_transcript('', duration=40)
        self.assertEqual(empty['stages']['live_human']['state'], 'unknown')
        short = ca.classify_transcript('', duration=3)
        self.assertEqual(short['stages']['live_human']['state'], 'no')
        reason = ca.reason_for({'status': 'done', 'phone': '79990000001', 'stages': short['stages'],
                                'low_confidence': False, 'speaker_uncertain': True,
                                'hiring_topic': False, 'scenario_hh_compatible': True})
        self.assertEqual((reason['code'], reason['group']), ('no_conversation', 'quality'))
        low = ca.classify_transcript(FULL, duration=40, confidence=0.31)
        self.assertTrue(low['low_confidence'])
        self.assertEqual(low['stages']['qualification']['state'], 'unknown')
        reason = ca.reason_for({'status': 'done', 'phone': '79990000001', 'stages': low['stages'],
                                'low_confidence': True, 'speaker_uncertain': False,
                                'hiring_topic': True, 'scenario_hh_compatible': True})
        self.assertEqual(reason['code'], 'uncertain_transcript')

    def test_answering_machine_is_quality_not_operator(self):
        result = ca.classify_transcript(ANSWER_MACHINE, duration=12, confidence=0.9)
        self.assertEqual(result['stages']['live_human']['state'], 'no')
        reason = ca.reason_for({'status': 'done', 'phone': '79990000001', 'stages': result['stages'],
                                'answering_machine': True, 'hiring_topic': False,
                                'low_confidence': False, 'speaker_uncertain': False,
                                'scenario_hh_compatible': True})
        self.assertEqual((reason['code'], reason['group']), ('answering_machine', 'quality'))

    def test_robot_kitchen_scenario_is_not_judged_by_hh_script(self):
        result = ca.classify_transcript(
            """Робот: Здравствуйте, это Мария, кухни на заказ.
Клиент: Да, кухня актуальна, ищем сотрудника.""", duration=30, confidence=0.9,
            kind='robot', scenario='kitchen', roles={'Робот': 'operator', 'Клиент': 'client'})
        self.assertFalse(result['scenario_hh_compatible'])
        self.assertEqual(result['stages']['hh_criteria']['state'], 'not_applicable')
        reason = ca.reason_for({'status': 'done', 'phone': '79990000001', 'stages': result['stages'],
                                'hiring_topic': result['hiring_topic'], 'low_confidence': False,
                                'speaker_uncertain': False, 'scenario_hh_compatible': False})
        self.assertEqual(reason['code'], 'scenario_mismatch')

    def test_confirmed_crm_lead_is_only_human_mark(self):
        result = ca.classify_transcript(FULL, duration=40, confidence=0.92)
        ca.apply_lead(result, None, has_reference=True)
        self.assertEqual(result['stages']['crm_lead']['state'], 'unknown')
        self.assertTrue(result['unknown_attribution'])
        ca.apply_lead(result, {'base': True, 'confirmed': False}, has_reference=True)
        self.assertEqual(result['stages']['crm_lead']['state'], 'no')
        ca.apply_lead(result, {'base': True, 'confirmed': True}, has_reference=True)
        self.assertEqual(result['stages']['crm_lead']['state'], 'yes')
        self.assertFalse(result['unknown_attribution'])


class SummaryTests(unittest.TestCase):
    def item(self, confirmed=False, **extra):
        result = ca.classify_transcript(FULL, duration=40, confidence=0.92)
        ca.apply_lead(result, {'base': True, 'confirmed': confirmed} if confirmed else None, True)
        item = {'stages': result['stages'], 'status': 'done', 'transcript_source': 'existing',
                'unknown_attribution': result['unknown_attribution'],
                'break': ca.reason_for({'status': 'done', 'phone': '79990000001', 'stages': result['stages'],
                                        'hiring_topic': True, 'low_confidence': False,
                                        'speaker_uncertain': False, 'scenario_hh_compatible': True})}
        item.update(extra)
        return item

    def test_summary_counts_and_reference_absent(self):
        summary = ca.summarize([self.item(False), self.item(False)], operator={'display': 'Ольга'}, period='01.11–02.11')
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['stages'][0]['reached'], 2)
        self.assertIsNone(summary['comparison'])
        self.assertIn('сравнение недоступно', summary['comparison_note'])

    def test_comparison_when_crm_has_leads(self):
        summary = ca.summarize([self.item(True), self.item(False)], operator={'display': 'Ольга'}, period='x')
        self.assertEqual(summary['comparison'], {'crm': 1, 'analysis': 2, 'both': 1, 'crm_only': 0, 'analysis_only': 1})

    def test_telegram_summary_has_no_contacts_or_transcript(self):
        summary = ca.summarize([self.item(True)], operator={'display': 'Ольга'}, period='01.11–02.11')
        text = ca.telegram_text(summary)
        self.assertNotIn('7999', text)
        self.assertNotIn('+7', text)
        self.assertNotIn('Здравствуйте', text)
        self.assertIn('Без контактов и полных записей.', text)


class AccessTests(unittest.TestCase):
    def test_only_owner_and_head_with_real_role(self):
        self.assertTrue(ca.can_manage(viewer(actor_role='owner')))
        self.assertTrue(ca.can_manage(viewer(actor_role='head')))
        self.assertFalse(ca.can_manage(viewer(actor_role='manager')))
        self.assertFalse(ca.can_manage(viewer(role='manager', actor_role='manager')))
        self.assertFalse(ca.can_manage(viewer(test=True)))
        self.assertFalse(ca.can_manage(viewer(support=True)))

    def test_only_moscow_period_and_operator_are_input(self):
        # Вход не принимает company/chat_id/author: всё берётся из сессии.
        import inspect
        source = inspect.getsource(ca.start)
        self.assertIn("getattr(actor, 'telegram_user_id'", source)
        self.assertIn('client_id = company_of(who)', source)


class FakePool:
    def __init__(self, *, fetchval=None, fetchrow=None, fetch=None):
        self._fetchval = fetchval
        self._fetchrow = fetchrow
        self._fetch = fetch
        self.executed = []

    async def fetchval(self, query, *args):
        return self._fetchval(query, *args) if callable(self._fetchval) else self._fetchval

    async def fetchrow(self, query, *args):
        return self._fetchrow(query, *args) if callable(self._fetchrow) else self._fetchrow

    async def fetch(self, query, *args):
        result = self._fetch(query, *args) if callable(self._fetch) else (self._fetch or [])
        if hasattr(result, '__await__'):
            result = await result
        return result

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return 'OK'


class CountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_are_scoped_by_session_company_and_unknown_attribution(self):
        from datetime import datetime, timezone
        seen = []

        class Pool(FakePool):
            async def fetchval(self, query, *args):
                return False  # CRM-таблиц для сверки нет: лид не подтверждён

            async def fetch(self, query, *args):
                seen.append((query, args))
                if 'FROM calls' in query:
                    return [{'id': 1, 'client_number': '79990000001', 'call_started_at': datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
                             'duration': 30, 'transcript': '', 'recording_id': None, 'status': 'skipped'}]
                if 'FROM employees' in query:
                    return []
                return []

        result = await ca.counts(Pool(), viewer(), 'oksana', '2026-11-01', '2026-11-02')
        self.assertTrue(result['configured'])
        self.assertEqual((result['calls'], result['leads'], result['unknown_attribution']), (1, 0, 1))
        call_query = next(args for query, args in seen if 'FROM calls' in query)
        self.assertEqual(call_query[:2], (1, '15'))  # компания сессии и добавочный Оксаны
        self.assertEqual(call_query[2].tzinfo, timezone.utc)
        self.assertEqual(call_query[3].isoformat(), '2026-11-02T21:00:00+00:00')


class TelegramAndIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_uses_only_session_owner_binding(self):
        owner = viewer(tg=555)
        pool = FakePool(fetchval=1)
        self.assertEqual(await ca.telegram_target(pool, owner), 555)
        self.assertIsNone(await ca.telegram_target(FakePool(fetchval=None), owner))
        self.assertIsNone(await ca.telegram_target(pool, viewer(tg=None)))
        self.assertIsNone(await ca.telegram_target(pool, viewer(role='manager', actor_role='manager', tg=555)))
        self.assertIsNone(await ca.telegram_target(pool, viewer(test=True)))

    async def test_send_rejects_missing_binding_before_touching_telegram(self):
        sent = []

        async def sender(chat_id, text):
            sent.append((chat_id, text))
            return True

        run = {'id': 1, 'client_id': 1, 'status': 'done', 'result': '{}', 'telegram_at': None, 'author_tg': None}
        pool = FakePool(fetchrow=run, fetchval=1)
        with self.assertRaises(PermissionError):
            await ca.send_telegram(pool, viewer(), 1, sender=sender)
        self.assertEqual(sent, [])

    async def test_send_uses_binding_of_run_author_not_current_sender(self):
        sent = []

        async def sender(chat_id, text):
            sent.append((chat_id, text))
            return True

        run = {'id': 1, 'client_id': 1, 'status': 'done', 'telegram_at': None, 'author_tg': 555,
               'result': '{"operator":{"display":"Ольга"},"period":"01.11–02.11","total":3,"stages":[],"coverage":{}}'}
        pool = FakePool(fetchrow=run, fetchval=1)
        result = await ca.send_telegram(pool, viewer(tg=777), 1, sender=sender)
        self.assertTrue(result['sent'])
        self.assertEqual([chat for chat, _ in sent], [555])  # не 777 текущего отправителя
        self.assertNotIn('7999', sent[0][1])
        self.assertTrue(any('telegram_by' in query for query, _ in pool.executed))

    async def test_other_company_run_is_invisible(self):
        pool = FakePool(fetchrow=None, fetchval=1)
        with self.assertRaises(ValueError):
            await ca.one(pool, viewer(), 99)

    async def test_rerun_same_period_is_reused_and_failed_can_retry(self):
        async def fetch(query, *args):
            return []
        queued = FakePool(fetch=fetch, fetchrow={'id': 9, 'status': 'queued'}, fetchval=1)
        answer = await ca.start(queued, viewer(), {'operator': 'robot', 'start': '2026-11-01', 'end': '2026-11-02'})
        self.assertEqual(answer, {'id': 9, 'reused': True, 'status': 'queued'})
        self.assertEqual(queued.executed, [])

        failed = FakePool(fetch=fetch, fetchrow={'id': 9, 'status': 'failed'}, fetchval=1)
        answer = await ca.start(failed, viewer(), {'operator': 'robot', 'start': '2026-11-01', 'end': '2026-11-02'})
        self.assertEqual((answer['id'], answer['reused'], answer['retried'], answer['status']), (9, True, True, 'queued'))
        self.assertEqual(len(failed.executed), 3)


class SchemaTests(unittest.TestCase):
    def test_schema_is_additive_and_has_dedup_and_item_uniqueness(self):
        from crm import call_analysis_schema
        sql = '\n'.join(call_analysis_schema.STATEMENTS)
        self.assertTrue(all('IF NOT EXISTS' in item for item in call_analysis_schema.STATEMENTS))
        self.assertIn('UNIQUE (client_id, dedup_key)', sql)
        self.assertIn('UNIQUE (run_id, call_ref)', sql)
        self.assertIn('telegram_by', sql)


if __name__ == '__main__':
    unittest.main()
