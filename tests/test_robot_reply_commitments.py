import unittest
from datetime import datetime, timezone
from crm.robot_reply_commitments import extract, parse_when

AT = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

class CommitmentTests(unittest.TestCase):
    def test_client_confirms_time(self):
        e = extract([('robot','Перезвонить завтра в 15:30?'),('client','Да, хорошо.')], AT, 'Europe/Moscow', 'callback')
        self.assertEqual(e['state'], 'agreed')
        self.assertEqual(e['callback_at'].isoformat(), '2026-11-17T15:30:00+03:00')

    def test_client_requests_and_robot_accepts(self):
        e = extract([('client','Перезвоните в пятницу в 16:00'),('robot','Договорились')], AT, 'Europe/Moscow')
        self.assertEqual(e['callback_at'].isoformat(), '2026-11-20T16:00:00+03:00')

    def test_missing_date_time_or_confirmation_not_booked(self):
        for phrase in ('Перезвонить в пятницу?', 'Перезвонить в 15:00?', 'Перезвонить завтра в 6?'):
            self.assertIsNone(extract([('robot',phrase),('client','Да')],AT,'Europe/Moscow'))
        self.assertIsNone(extract([('robot','Перезвонить завтра в 15:00?'),('client','Да, но нет, не надо')],AT,'Europe/Moscow'))
        self.assertIsNone(extract([('robot','Перезвонить завтра в 15:00?'),('client','Нет')],AT,'Europe/Moscow'))

    def test_decline_wins_over_earlier_agreement(self):
        e=extract([('robot','Перезвонить завтра в 15:00?'),('client','Да'),('client','Больше не звоните')],AT,'Europe/Moscow')
        self.assertEqual(e['state'],'withdrawn')
        self.assertIsNone(e['callback_at'])

    def test_latest_agreement_within_call(self):
        e=extract([('robot','Перезвонить завтра в 15:00?'),('client','Да'),('robot','Лучше завтра в 16:00 перезвонить?'),('client','Да')],AT,'Europe/Moscow')
        self.assertEqual(e['callback_at'].hour,16)

    def test_absolute_and_zone(self):
        self.assertEqual(parse_when('перезвонить 20.11.2026 в 18:00',AT,'Europe/Moscow').day,20)
        self.assertIsNone(parse_when('завтра в 18:00',AT,'Not/AZone'))
        self.assertIsNone(parse_when('завтра в 18:00',AT.replace(tzinfo=None),'Europe/Moscow'))

    def test_automated_facts_are_not_confirmation(self):
        self.assertIsNone(extract([('robot','Завтра в 15:00 перезвонить?')],AT,'Europe/Moscow'))

if __name__ == '__main__': unittest.main()
