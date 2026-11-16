import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_crm_personal import PersonalHTTPTests, DSN


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class ConnectionsHTTPTests(PersonalHTTPTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.pool.execute((Path(__file__).resolve().parents[1] / 'migrations/049_crm_personal_connections.sql').read_text(encoding='utf-8'))
        await self.pool.execute('TRUNCATE crm_personal_connections')

    async def test_drafts_owner_isolation_and_version(self):
        self.login(123)
        await self.request_json('PUT', '/api/personal/profile', {'display_name':'Первый','timezone':'Europe/Moscow'})
        await self.pool.execute("INSERT INTO crm_personal_connections(telegram_user_id,client_id,kind,provider,email) VALUES(123,2,'mail','google','other@example.org')")
        code, empty = await self.request_json('GET','/api/personal/connections')
        self.assertEqual((code, empty['connections']), (200, {}))
        draft={'provider':'yandex','email':'one@example.org','display_name':'Имя','timezone':'','version':0}
        code, saved=await self.request_json('PUT','/api/personal/connections/mail',draft)
        self.assertEqual(code,200); self.assertEqual(saved['connection']['version'],1)
        code, _=await self.request_json('PUT','/api/personal/connections/mail',draft)
        self.assertEqual(code,409)
        draft.update(version=1,display_name='Новое имя')
        code, saved=await self.request_json('PUT','/api/personal/connections/mail',draft)
        self.assertEqual(code,200); self.assertEqual(saved['connection']['version'],2)
        self.login(124)
        await self.request_json('PUT', '/api/personal/profile', {'display_name':'Второй','timezone':'Europe/Moscow'})
        code, other=await self.request_json('GET','/api/personal/connections')
        self.assertEqual((code,other['connections']),(200,{}))
        code, _=await self.request_json('PUT','/api/personal/connections/mail',draft)
        self.assertEqual(code,409)

    async def test_validation_and_support(self):
        from crm import auth
        self.login(123)
        await self.request_json('PUT', '/api/personal/profile', {'display_name':'Первый','timezone':'Europe/Moscow'})
        valid={'provider':'google','email':'one@example.org','calendar_name':'','timezone':'','version':0}
        for mutation in ({'provider':'bad'}, {'email':'bad'}, {'password':'secret'}, {'token':'x'}, {'timezone':'Invalid/Zone'}):
            code, _ = await self.request_json('PUT','/api/personal/connections/calendar',{**valid,**mutation})
            self.assertEqual(code,400)
        await self.pool.execute("UPDATE employees SET archived_at=now() WHERE telegram_user_id=123 AND client_id=1")
        code, _=await self.request_json('GET','/api/personal/connections')
        self.assertIn(code,(401,403))
        await self.pool.execute("UPDATE employees SET archived_at=NULL WHERE telegram_user_id=123 AND client_id=1")
        admin=await self.pool.fetchval("INSERT INTO crm_invites(token_hash,role,client_id,platform) VALUES('support-connect','director',1,true) RETURNING id")
        await self.pool.execute('UPDATE crm_sessions SET support_by=$1 WHERE token_hash=$2',admin,auth.digest('session123'))
        code, _=await self.request_json('GET','/api/personal/connections')
        self.assertEqual(code,403)
        code, _=await self.request_json('PUT','/api/personal/connections/calendar',valid)
        self.assertEqual(code,403)
        with patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS':''}):
            code, _=await self.request_json('GET','/api/personal/connections')
            self.assertEqual(code,403)
