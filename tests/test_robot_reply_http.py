"""HTTP security and audio integration on the existing isolated CRM test database."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
from aiohttp.test_utils import TestClient, TestServer
from tests.test_crm import DBCase, DSN
from crm import auth, web, robot, robot_reply_learning


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL not set')
class ReplyHTTPTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.env = patch.dict(os.environ, {'CRM_DEMO_INSTANCE':'1', 'CRM_HOME_COMPANY':'1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.data = patch.object(robot, 'DATA', Path(self.tmp.name)); self.data.start(); self.addCleanup(self.data.stop)

    async def login(self, tg, token):
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at) VALUES($1,$2,now()+'1 day')", auth.digest(token), tg)
        self.http.session.cookie_jar.clear()
        self.http.session.cookie_jar.update_cookies({'crm_session':token})

    async def test_project_evidence_and_access(self):
        base='/api/robot/cab'
        unauth = await self.http.get(base+'/reply-projects')
        self.assertIn(unauth.status, (401,403))
        await self.login(901,'head-reply')
        denied = await self.http.post(base+'/reply-projects', json={'name':'Упаковка','profile':'packaging'})
        self.assertEqual(denied.status,403)
        h={'X-CRM':'1'}
        created = await self.http.post(base+'/reply-projects', json={'name':'Упаковка','profile':'packaging'}, headers=h)
        self.assertEqual(created.status,200, await created.text())
        pid=(await created.json())['id']
        listing=await self.http.get(base+'/reply-projects')
        self.assertTrue(any(x['id']==pid and x['profile']=='packaging' for x in (await listing.json())['items']))
        draft=await self.http.post(base+'/replies', json={'project_id':pid,'question':'Какая коробка?','answer':'Уточню.'},headers=h)
        self.assertEqual(draft.status,200,await draft.text()); cid=(await draft.json())['id']
        await self.pool.execute("UPDATE robot_reply_versions SET fact_needed=true WHERE candidate_id=$1",cid)
        reject=await self.http.post(f'{base}/replies/{cid}/approve',json={'version':1},headers=h)
        self.assertEqual(reject.status,400)
        await self.pool.execute("INSERT INTO robot_calls(call_id,owner,operator,at,recording) VALUES('evidence-http','','', $1,'test.wav')",datetime.now(timezone.utc))
        await robot_reply_learning.ingest_source(self.pool,'',pid,'robot','evidence-http',[])
        directory=Path(self.tmp.name)/'recordings'; directory.mkdir(); (directory/'test.wav').write_bytes(b'RIFF0123456789')
        url=f'{base}/replies/{cid}/evidence/robot/evidence-http?project_id={pid}'
        self.assertEqual((await self.http.get(url)).status,404)
        link=await self.http.post(f'{base}/replies/{cid}/evidence',json={'project_id':pid,'source_type':'robot','source_id':'evidence-http'},headers=h)
        self.assertEqual(link.status,200,await link.text())
        audio=await self.http.get(url,headers={'Range':'bytes=2-5'})
        self.assertEqual(audio.status,206); self.assertEqual(await audio.read(),b'FF01')
        await robot_reply_learning.ingest_source(self.pool,'',pid,'human','42',[])
        human_link=await self.http.post(f'{base}/replies/{cid}/evidence',json={'project_id':pid,'source_type':'human','source_id':'42'},headers=h)
        self.assertEqual(human_link.status,200)
        human_url=f'{base}/replies/{cid}/evidence/human/42?project_id={pid}'
        with patch('crm.recording.get',new=AsyncMock(return_value=(b'0123456789','audio/wav'))):
            audio=await self.http.get(human_url,headers={'Range':'bytes=2-5'})
            self.assertEqual(audio.status,206); self.assertEqual(await audio.read(),b'2345')
        await self.login(123,'manager-reply')
        self.assertEqual((await self.http.get(base+'/reply-projects')).status,403)
        self.assertEqual((await self.http.get(url)).status,403)
        token='demo-reply'
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,is_test) VALUES($1,now()+'1 day','director',1,true)", auth.digest(token))
        self.http.session.cookie_jar.clear(); self.http.session.cookie_jar.update_cookies({'crm_session':token})
        self.assertEqual((await self.http.get(base+'/reply-projects')).status,403)
