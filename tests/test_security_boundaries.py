"""Regressions for local CRM authentication, upload, logging, and exports."""

import asyncio
import csv
import io
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from tests import test_crm  # Sets synthetic credentials before importing CRM web.
from crm import auth, callcenter_db, robot_store, web


class PasswordRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_password_checks_share_the_attempt_limit(self):
        login = 'security.fixture'
        count = 2 * auth.MAX_FAILS
        all_waiting = asyncio.Event()
        waiting = 0

        async def fetchrow(*_):
            nonlocal waiting
            waiting += 1
            if waiting == count:
                all_waiting.set()
            await all_waiting.wait()
            return {'id': 1, 'password_hash': 'synthetic'}

        pool = AsyncMock()
        pool.fetchrow.side_effect = fetchrow
        with patch.dict(auth._fails, clear=True), patch.object(auth, 'password_ok', return_value=False) as verify:
            results = await asyncio.wait_for(asyncio.gather(
                *(auth.password_login(pool, login, 'wrong') for _ in range(count)), return_exceptions=True), timeout=5)
            self.assertTrue(all(isinstance(result, ValueError) for result in results))
            self.assertEqual(verify.call_count, auth.MAX_FAILS)

    async def test_unknown_login_does_not_allocate_attempt_state(self):
        pool = AsyncMock()
        pool.fetchrow.return_value = None
        with patch.dict(auth._fails, clear=True):
            with self.assertRaises(ValueError):
                await auth.password_login(pool, 'nonexistent.fixture', 'wrong')
            self.assertFalse(auth._fails)


@unittest.skipUnless(test_crm.DSN, 'local audit database required')
class PasswordResetRaceTests(test_crm.DBCase):
    async def test_old_password_cannot_issue_session_after_reset(self):
        login = 'security.rotate'
        old = await auth.account_create(self.pool, 1, 'manager', login, extension='101')
        invite_id = await self.pool.fetchval('SELECT id FROM crm_invites WHERE login=$1', login)
        session, _ = await auth.password_login(self.pool, login, old)
        self.assertIsNotNone(await auth.viewer_for(self.pool, session))
        verified = asyncio.Event()
        resume = asyncio.Event()
        issue = auth._open_session

        async def delayed_issue(*args, **kwargs):
            verified.set()
            await resume.wait()
            return await issue(*args, **kwargs)

        with patch.object(auth, '_open_session', delayed_issue):
            pending = asyncio.create_task(auth.password_login(self.pool, login, old))
            try:
                await asyncio.wait_for(verified.wait(), timeout=5)
                changed = await auth.account_change(self.pool, 1, invite_id, new=True)
                self.assertEqual(changed['login'], login)
                self.assertIsNone(await auth.viewer_for(self.pool, session))
            finally:
                resume.set()
            with self.assertRaisesRegex(ValueError, 'Неверный логин или пароль'):
                await asyncio.wait_for(pending, timeout=5)
        new_session, role = await auth.password_login(self.pool, login, changed['password'])
        self.assertEqual(role, 'manager')
        self.assertIsNotNone(await auth.viewer_for(self.pool, new_session))


class HttpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = TestClient(TestServer(web.build(AsyncMock())))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)

    async def test_webhook_error_redacts_path_and_exception(self):
        token = 'synthetic-hook-token'
        incoming = AsyncMock(side_effect=RuntimeError(token))
        with self.assertLogs('crm', 'ERROR') as captured, patch.object(web.automations, 'incoming', incoming):
            response = await self.http.post(f'/hook/{token}', json={})
        self.assertEqual(response.status, 500)
        self.assertTrue(all(token not in line for line in captured.output))

    async def test_voice_clone_bounds_chunked_fields_and_accepts_normal_form(self):
        async def large_name():
            yield b'--audit\r\nContent-Disposition: form-data; name="name"\r\n\r\n' + b'x' * 2048
            yield b'\r\n--audit--\r\n'

        clone = AsyncMock(return_value={'ok': True})
        headers = {'X-CRM': '1'}
        with patch.object(web.auth, 'viewer_for', AsyncMock(return_value=None)), \
                patch.object(web, 'robot_director', AsyncMock(return_value='synthetic director')), \
                patch.object(web.robot, 'voices_clone', clone), patch.object(web.robot, 'CLONE_MAX_BYTES', 1024):
            response = await self.http.post('/api/robot/voices/clone', data=large_name(),
                                            headers={**headers, 'Content-Type': 'multipart/form-data; boundary=audit'})
            self.assertEqual(response.status, 400)
            clone.assert_not_awaited()
            form = FormData()
            form.add_field('name', 'Пример')
            form.add_field('files', b'example', filename='sample.wav')
            response = await self.http.post('/api/robot/voices/clone', data=form, headers=headers)
            self.assertEqual(response.status, 200)
            clone.assert_awaited_once_with('synthetic director', 'Пример', [('sample.wav', b'example')])


class CsvBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_callcenter_and_robot_exports_keep_formulas_as_text(self):
        with patch.object(callcenter_db, 'cc_client', AsyncMock()), \
                patch.object(callcenter_db.DbSource, 'rows', AsyncMock(return_value=[['=1+2', 'Обычный текст']])):
            text = await callcenter_db.export_csv(None, 1, 'Synthetic')
        row = next(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
        self.assertEqual(row, ["'=1+2", 'Обычный текст'])
        text = robot_store.calls_csv([{'at': '2026-11-07T10:00', 'phone': '79990000000', 'campaign': ' @test',
                                       'outcome': 'done', 'seconds': 10, 'comment': '=1+2', 'cost': 1}])
        row = list(csv.reader(io.StringIO(text), delimiter=';'))[1]
        self.assertEqual((row[2], row[4], row[5]), ("' @test", '10', "'=1+2"))
