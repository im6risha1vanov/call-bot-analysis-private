"""The queue should keep completed speech recognition when analysis fails."""
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg

os.environ.setdefault('CVC_API_KEY', 'synthetic-test-key')
os.environ.setdefault('BOT_TOKEN', '123456:synthetic_test_token')

import queue_runner
from crm.overview import analysis_progress
from handlers import analyze_call as handler


class CallReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_retry_reuses_transcript_even_when_no_speech_was_recognized(self):
        task = {'id': 42, 'client_id': 1, 'input': json.dumps({'call_id': 7})}
        client = {'id': 1, 'processing_enabled': True, 'vpbx_api_key_enc': '', 'vpbx_api_salt_enc': ''}
        for transcript in ('Синтетическая реплика.', ''):
            with self.subTest(transcript=transcript):
                call = {'id': 7, 'client_id': 1, 'status': 'new', 'extension': '11',
                        'transcript': None, 'recording_id': 'test-recording'}
                pool = MagicMock()
                pool.acquire.return_value.__aenter__.return_value = pool
                pool.fetchrow = AsyncMock(side_effect=[call, client, None, {**call, 'transcript': transcript}, client, None])
                pool.execute = AsyncMock()
                download = AsyncMock(return_value=b'test-audio')
                transcribe = AsyncMock(return_value=(transcript, 60))

                async def fail_analysis(*args, **kwargs):
                    pool.execute.assert_any_await("UPDATE calls SET transcript=$2 WHERE id=$1", 7, transcript)
                    raise RuntimeError('Astra 429: synthetic failure')

                with patch('unified.analysis_policy.pin_task', AsyncMock(return_value={})), \
                        patch.object(handler, '_over_daily_limit', AsyncMock(return_value=False)), \
                        patch.object(handler, '_model_retry_at', 0), \
                        patch.object(handler, 'decrypt', return_value='test'), \
                        patch.object(handler.mango_client, 'fetch_recording', download), \
                        patch.object(handler, 'transcribe_bytes', transcribe), \
                        patch.object(handler.review_kitchen, 'handles', return_value=False), \
                        patch.object(handler.review_engine, 'enabled', return_value=True), \
                        patch.object(handler, '_review_meta', AsyncMock(return_value={})), \
                        patch.object(handler.review_prompts, 'run_common_review', AsyncMock(side_effect=fail_analysis)), \
                        self.assertLogs(handler.log, level='ERROR'):
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError, 'Astra 429'):
                            await handler.analyze_call(pool, task)
                download.assert_awaited_once()
                transcribe.assert_awaited_once()

    async def test_queue_records_delay_and_clears_stale_error_after_success(self):
        task = {'id': 42, 'client_id': 1, 'type': 'analyze_call'}
        pool = MagicMock(execute=AsyncMock())
        reason = 'Модель Astra временно недоступна (HTTP 404)'
        delay = timedelta(minutes=15)
        analyze = AsyncMock(side_effect=[queue_runner.RetryLater(delay, reason, record_limit=False), {}])

        with patch.object(queue_runner, 'claim_next', AsyncMock(return_value=task)), \
                patch.dict(queue_runner._REGISTRY, {'analyze_call': analyze}):
            self.assertTrue(await queue_runner.run_once(pool))
            self.assertTrue(await queue_runner.run_once(pool))

        self.assertEqual(pool.execute.await_count, 2)
        deferred, completed = (call.args for call in pool.execute.await_args_list)
        self.assertIn('error=$3', deferred[0])
        self.assertEqual(deferred[1:], (42, delay, reason))
        self.assertIn('error=NULL', completed[0])
        self.assertEqual(completed[1:], (42, '{}'))

    @unittest.skipUnless(os.getenv('METHODOLOGY_TEST_DATABASE_URL'), 'Requires isolated PostgreSQL')
    async def test_progress_separates_failed_jobs_from_calls_awaiting_analysis(self):
        conn = await asyncpg.connect(os.environ['METHODOLOGY_TEST_DATABASE_URL'])
        try:
            self.assertTrue((await conn.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
            await conn.execute("""
                CREATE TEMP TABLE calls (id bigint, client_id int, extension text, status text,
                                         direction int DEFAULT 2, call_started_at timestamptz DEFAULT now());
                CREATE TEMP TABLE tasks (id bigint, client_id int, type text, status text, input jsonb);
                INSERT INTO calls (id,client_id,extension,status) VALUES
                  (1,1,'11','new'), (2,1,'11','new'), (3,1,'11','new'), (4,1,'11','analyzed'),
                  (5,1,'11','awaiting_record'), (6,1,'11','no_recording'), (7,1,'11','skipped'),
                  (8,2,'11','failed'), (9,1,'14','analyzed');
                INSERT INTO tasks VALUES
                  (1,1,'analyze_call','failed','{"call_id":1}'), (2,1,'analyze_call','new','{"call_id":2}'),
                  (3,2,'analyze_call','failed','{"call_id":3}'), (4,1,'evaluate_course','failed','{"call_id":3}'),
                  (5,1,'analyze_call','processing','{"call_id":2}'), (6,1,'analyze_call','failed','{"call_id":4}');
            """)
            now = await conn.fetchval("SELECT now() + interval '1 second'")
            self.assertEqual(await analysis_progress(conn, 1, now - timedelta(days=1), now, ['11']), {
                '11': {'processed': 1, 'queued': 1, 'awaiting_record': 1,
                       'no_recording': 1, 'failed': 1, 'unqueued': 1},
            })
        finally:
            await conn.close()
