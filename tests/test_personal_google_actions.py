import json
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from crypto_util import encrypt, decrypt
from crm import personal_google_actions as a, personal_google as g
from tests.test_personal_google import GoogleDatabaseTests, TEST_DSN, safe_test_dsn


class PayloadTests(unittest.TestCase):
 def test_header_injection_and_preview(self):
  good={'to':'alice@example.com','subject':'Привет','body':'Текст'}
  self.assertEqual(a.payload(good),good)
  for changed in ({'to':'a@example.com\r\nBcc:b@example.com'}, {'subject':'hi\r\nBcc: b@example.com'}, {'body':''}, {'to':'one@example.com,two@example.com'}):
   with self.assertRaises(ValueError): a.payload({**good,**changed})

@unittest.skipUnless(TEST_DSN and safe_test_dsn(TEST_DSN),'requires disposable localhost google_test database')
class ActionTests(GoogleDatabaseTests):
 async def asyncSetUp(self):
  await super().asyncSetUp()
  sql=(Path(__file__).resolve().parents[1]/'migrations/061_crm_personal_google_actions.sql').read_text(encoding='utf-8-sig')
  await self.pool.execute(sql)
  await self.pool.execute("INSERT INTO crm_personal_google_write_grants VALUES(123,1,'mail',1,'sub','a@example.com',$1,$2,'v1')",a.SEND_SCOPE,encrypt('refresh'))
 async def test_send_once_and_owner_binding(self):
  async with self.pool.acquire() as conn:
   draft=await a.prepare(conn,123,1,{'to':'to@example.com','subject':'s','body':'message'})
   with self.assertRaises(ValueError): await a.confirm(conn,124,1,draft['id'],draft['payload_hash'])
   with self.assertRaises(ValueError): await a.confirm(conn,123,1,draft['id'],'wrong')
   with patch.object(a.google,'provider_json',AsyncMock(return_value={'access_token':'access','token_type':'Bearer','refresh_token':'rotated'})), patch.object(a,'send_provider',AsyncMock(return_value=('sent','message-id'))) as send:
    result=await a.confirm(conn,123,1,draft['id'],draft['payload_hash']); self.assertEqual(result['status'],'sent')
    with self.assertRaises(ValueError): await a.confirm(conn,123,1,draft['id'],draft['payload_hash'])
    send.assert_awaited_once()
   self.assertEqual(await conn.fetchval('SELECT provider_id FROM crm_personal_google_actions'), 'message-id')
   enc=await conn.fetchval('SELECT refresh_enc FROM crm_personal_google_write_grants'); self.assertEqual(decrypt(enc),'rotated')
 async def test_unknown_and_stale(self):
  async with self.pool.acquire() as conn:
   data={'to':'to@example.com','subject':'s','body':'message'}
   draft=await a.prepare(conn,123,1,data)
   await conn.execute('UPDATE crm_personal_connections SET version=version+1')
   with self.assertRaises(ValueError): await a.confirm(conn,123,1,draft['id'],draft['payload_hash'])
   await conn.execute('UPDATE crm_personal_connections SET version=version-1')
   with patch.object(a.google,'provider_json',AsyncMock(return_value={'access_token':'access','token_type':'Bearer'})),patch.object(a,'send_provider',AsyncMock(side_effect=TimeoutError())):
    result=await a.confirm(conn,123,1,draft['id'],draft['payload_hash']); self.assertEqual(result['status'],'unknown')
   with self.assertRaises(ValueError): await a.confirm(conn,123,1,draft['id'],draft['payload_hash'])
 async def test_consent_to_confirm_and_disconnect(self):
  from urllib.parse import urlsplit,parse_qs
  await self.pool.execute('DELETE FROM crm_personal_google_write_grants')
  headers={'X-CRM':'1'}
  res=await self.client.post('/api/personal/google/mail/send-consent',headers=headers); self.assertEqual(res.status,200)
  url=(await res.json())['authorization_url']; params=parse_qs(urlsplit(url).query)
  self.assertIn(a.SEND_SCOPE,params['scope'][0]); self.assertNotIn(g.SCOPES['mail'],params['scope'][0])
  token={'access_token':'access','refresh_token':'new-refresh','token_type':'Bearer','scope':'openid email '+a.SEND_SCOPE}
  with patch.object(a.google,'provider_json',AsyncMock(side_effect=[token,{'sub':'subject','email':'a@example.com','email_verified':True}])):
   res=await self.client.get('/api/personal/google/callback',params={'state':params['state'][0],'code':'test'}); self.assertEqual(res.status,200)
  data={'to':'one@example.com','subject':'subject','body':'body'}
  res=await self.client.post('/api/personal/google/mail/send/prepare',headers=headers,json=data); self.assertEqual(res.status,200); draft=await res.json(); self.assertEqual(draft['preview'],data)
  confirmation={'id':draft['id'],'payload_hash':draft['payload_hash']}
  res=await self.client.post('/api/personal/google/mail/send/confirm',headers={'X-CRM':'1','User':'124'},json=confirmation); self.assertEqual(res.status,409)
  res=await self.client.post('/api/personal/google/mail/send/confirm',json=confirmation); self.assertEqual(res.status,403)
  with patch.object(a.google,'provider_json',AsyncMock(return_value={'access_token':'a','token_type':'Bearer'})),patch.object(a,'send_provider',AsyncMock(return_value=('sent','m1'))) as sent:
   res=await self.client.post('/api/personal/google/mail/send/confirm',headers=headers,json=confirmation); self.assertEqual(res.status,200); self.assertEqual((await res.json())['status'],'sent')
   res=await self.client.post('/api/personal/google/mail/send/confirm',headers=headers,json=confirmation); self.assertEqual(res.status,409); sent.assert_awaited_once()
  res=await self.client.post('/api/personal/google/mail/send/prepare',headers=headers,json=data); stale=await res.json()
  with patch.object(a.google,'provider_json',AsyncMock(return_value={})):
   res=await self.client.delete('/api/personal/google/mail',headers=headers); self.assertEqual(res.status,200)
  res=await self.client.post('/api/personal/google/mail/send/confirm',headers=headers,json={'id':stale['id'],'payload_hash':stale['payload_hash']}); self.assertEqual(res.status,409)
