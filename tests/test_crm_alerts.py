import json
import os
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts import crm_alerts as alerts


class AlertTests(unittest.TestCase):
    def test_journal_matches_python_and_systemd_errors_without_returning_raw_data(self):
        rows = [
            {'_SYSTEMD_UNIT': 'callbot-crm.service', 'PRIORITY': '6',
             'MESSAGE': '2026-11-09 09:01:02,123 ERROR arbitrary secret and URL'},
            {'_SYSTEMD_UNIT': 'init.scope', 'UNIT': 'callbot-crm.service', 'PRIORITY': '3'},
            {'_SYSTEMD_UNIT': 'callbot-crm.service', 'PRIORITY': '6', 'MESSAGE': 'INFO arbitrary ERROR input'},
            {'_SYSTEMD_UNIT': 'other.service', 'PRIORITY': '3'},
            {'_SYSTEMD_UNIT': 'callbot-crm.service', 'PRIORITY': '3', '__REALTIME_TIMESTAMP': '100000000'},
        ]
        stdout = '\n'.join(json.dumps({'__REALTIME_TIMESTAMP': '110000000', **row}) for row in rows)
        with patch.object(alerts.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout)):
            self.assertEqual(alerts.read_journal(100, 120), Counter({'log:callbot-crm.service': 2}))

    def test_state_aggregates_retries_and_debounces(self):
        issue = 'log:callbot-crm.service'
        with (patch.object(alerts, 'read_journal', side_effect=[Counter({issue: 2}), Counter(), Counter({issue: 3})]) as read,
              patch.object(alerts, 'inactive_units', return_value=set()),
              patch.object(alerts, 'api_ready', return_value=False),
              patch.object(alerts, 'send', side_effect=[False, True, True]) as send):
            state = {}
            alerts.poll(state, 1000, 8095)
            read.assert_called_with(940, 1000)
            self.assertEqual(state['pending'], {issue: 2})
            self.assertFalse(alerts.deliver(state, 1000, 'synthetic', 42))
            alerts.poll(state, 1060, 8095)
            read.assert_called_with(1000, 1060)
            self.assertEqual(state['pending'], {issue: 2, 'http': 1})
            self.assertTrue(alerts.deliver(state, 1060, 'synthetic', 42))
            self.assertEqual(send.call_count, 1)
            self.assertTrue(alerts.deliver(state, 1300, 'synthetic', 42))
            self.assertIn('CRM — локальный API', send.call_args.args[2])
            self.assertEqual(state['pending'], {})
            alerts.poll(state, 1360, 8095)
            self.assertTrue(alerts.deliver(state, 1360, 'synthetic', 42))
            self.assertEqual(send.call_count, 2)
            self.assertTrue(alerts.deliver(state, 2200, 'synthetic', 42))
            self.assertEqual(send.call_count, 3)
            self.assertIn('(3)', send.call_args.args[2])

    def test_missing_systemd_and_failed_journal_are_reported_without_advancing_checkpoint(self):
        state = {'checked_until': 900}
        with (patch.object(alerts, 'read_journal', side_effect=subprocess.TimeoutExpired('journalctl', 7)),
              patch.object(alerts, 'inactive_units', side_effect=RuntimeError('internal data')),
              patch.object(alerts, 'api_ready', return_value=True)):
            alerts.poll(state, 1000, 8095)
        self.assertEqual(state['checked_until'], 900)
        self.assertEqual(state['pending'], {'journal': 1, 'systemd': 1})

    def test_recipient_validation_and_retry_state_survives_restart(self):
        issue = 'log:callbot-astra.service'
        with tempfile.TemporaryDirectory() as tmp:
            env = {'BOT_TOKEN': '123:syn-thetic', 'CRM_ALERT_CHAT_ID': '-42', 'STATE_DIRECTORY': tmp}
            state_path = Path(tmp) / 'state.json'
            with patch.dict(os.environ, env, clear=True), patch('sys.stderr'):
                self.assertEqual(alerts.main(), 2)
                self.assertFalse(state_path.exists())
            env['CRM_ALERT_CHAT_ID'] = '42'
            with (patch.dict(os.environ, env, clear=True),
                  patch.object(alerts.time, 'time', side_effect=[1000, 1300]),
                  patch.object(alerts, 'read_journal', side_effect=[Counter({issue: 1}), Counter()]),
                  patch.object(alerts, 'inactive_units', return_value=set()),
                  patch.object(alerts, 'api_ready', return_value=True),
                  patch.object(alerts, 'send', side_effect=[False, True]),
                  patch('sys.stderr')):
                self.assertEqual(alerts.main(), 1)
                self.assertEqual(json.loads(state_path.read_text())['pending'], {issue: 1})
                self.assertEqual(alerts.main(), 0)
                self.assertEqual(json.loads(state_path.read_text())['pending'], {})

    def test_telegram_payload_and_api_result(self):
        with patch.object(alerts.HTTP, 'open') as open_request:
            response = open_request.return_value.__enter__.return_value
            response.read.side_effect = [b'{"ok":true}', b'{"ok":false}', b'[]']
            self.assertTrue(alerts.send('123:syn-thetic', 42, 'safe diagnostic'))
            request = open_request.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.telegram.org/bot123:syn-thetic/sendMessage')
            self.assertEqual(json.loads(request.data), {'chat_id': 42, 'text': 'safe diagnostic'})
            self.assertFalse(alerts.send('123:syn-thetic', 42, 'safe diagnostic'))
            self.assertFalse(alerts.send('123:syn-thetic', 42, 'safe diagnostic'))
