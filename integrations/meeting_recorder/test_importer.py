import json, tempfile, unittest, os
from unittest.mock import patch
from pathlib import Path
import importer as m

class Tests(unittest.TestCase):
 def test_snapshot(self):
  with tempfile.TemporaryDirectory() as d:
   b=Path(d); sid='20261119T120000Z-1234abcd'; event='1234567890123'; p=b/'sessions'/sid; p.mkdir(parents=True); (b/'teams'/'events').mkdir(parents=True)
   def put(path,obj): path.write_text(json.dumps(obj),encoding='utf-8')
   state={'id':sid,'mode':'meeting','state':'transcription_partial','created_at':'2026-11-19T12:00:00Z','teams_event_id':event}
   put(p/'status.json',state); put(b/'teams'/'events'/(event+'.json'),{'chat_id':m.CHAT_ID,'message_id':event})
   raw={'session_id':sid,'utterances':[{'source':'s','start':1,'end':2,'text':'A'}], 'errors':[]}; put(p/'transcript.json',raw)
   ledger={event:{'state':'started','session_id':sid}}
   a=m.snapshot(b,sid,ledger,0); self.assertEqual(a['state']['state'],'transcription_partial'); self.assertIsNone(m.snapshot(b,sid,ledger,m.parse_time('2026-11-20T00:00:00Z')))
   ident=a['transcript']['utterances'][0]['id']; raw['utterances'].insert(0,{'source':'s','start':0,'end':1,'text':'B'}); put(p/'transcript.json',raw)
   c=m.snapshot(b,sid,ledger,0); self.assertEqual(ident,c['transcript']['utterances'][1]['id']); self.assertNotEqual(a['revision'],c['revision'])
   ledger[event]['state']='failed'; self.assertIsNone(m.snapshot(b,sid,ledger,0))
 def test_limit(self):
  rows=[{'id':'a','text':'x'*5000},{'id':'b','text':'ok'}]
  p={'state':{},'transcript':{'utterances':rows},'report':{'notes':{'decisions':[{'evidence_ids':['b']}]}},'revision':'x'}
  v=m.fit_payload(p,1800); self.assertEqual([u['id'] for u in v['transcript']['utterances']],['b']); self.assertTrue(v['transcript']['export_truncated']); self.assertLessEqual(len(m.canonical(v)),1800)
 def test_credential_snapshot_path(self):
  with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': '/private/credentials'}):
   self.assertEqual(m.ledger_path(Path('/fixture')), Path('/private/credentials/meeting_dispatch'))
  with patch.dict(os.environ, {}, clear=True):
   self.assertEqual(m.ledger_path(Path('/fixture')), Path('/fixture/teams/dispatch-ledger.json'))
 def test_url(self):
  with self.assertRaises(ValueError): m.send('http://example.com:80/api/internal/team/meetings/import','secret',{})
if __name__=='__main__': unittest.main()
