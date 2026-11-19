import os
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from aiohttp import web
from crm import team_meetings as m


def snapshot():
    return {'schema_version': 1, 'source': 'teams_auto', 'session_id': 's1', 'revision': 'a'*64,
            'source_updated_at': '2026-11-19T12:00:00Z', 'state': {'state': 'complete'},
            'transcript': {'utterances': [{'id':'u-1','text':'Пример'}]},
            'report': {'notes': {'decisions': [{'text':'Сделать', 'evidence_ids':['u-1']}]}}}

class Validation(unittest.TestCase):
    def test_evidence_ids_and_stable_items(self):
        data = snapshot()
        m.validate(data)
        self.assertEqual(m.items(data['report']), m.items(data['report']))
        data['report']['notes']['decisions'][0]['evidence_ids'] = ['u-missing']
        with self.assertRaises(ValueError): m.validate(data)

    def test_source_config_fail_closed(self):
        with patch.dict(os.environ, {'CRM_MEETINGS_SOURCES_JSON':'{}'}):
            with self.assertRaises(Exception): m._source_config('teams_auto')

class DB:
    def __init__(self, old): self.old=old; self.updates=[]
    async def execute(self, sql, *args): self.updates.append(sql)
    async def fetchrow(self, sql, *args): return self.old

class Import(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {'CRM_MEETINGS_SOURCES_JSON':'{"teams_auto":{"company_id":1,"series_id":"weekly","owner_ids":[3]}}'})
    async def test_duplicate_and_stale(self):
        old = {'id':1, 'company_id':1, 'series_id':'weekly', 'revision':'a'*64,
               'source_updated_at':datetime(2026,11,19,12,tzinfo=timezone.utc)}
        db = DB(old)
        self.assertEqual((await m.import_snapshot(db, snapshot()))['result'], 'duplicate')
        data = snapshot(); data['revision']='b'*64
        with self.assertRaises(web.HTTPConflict): await m.import_snapshot(db, data)
        self.assertFalse(any(x.lstrip().startswith('UPDATE') for x in db.updates))

    @patch.dict(os.environ, {'CRM_MEETINGS_SOURCES_JSON':'{"teams_auto":{"company_id":1,"series_id":"weekly","owner_ids":[3]}}'})
    async def test_access_is_company_and_owner_scoped(self):
        class Conn:
            async def fetchrow(self, sql, *args):
                self.args=args
                return {'owner_ids':[3], 'source':'teams_auto', 'company_id':1, 'series_id':'weekly'} if args[1] == 1 else None
        conn=Conn()
        with self.assertRaises(Exception): await m._meeting(conn, (2,3), 7)
        with self.assertRaises(Exception): await m._meeting(conn, (1,4), 7)
        self.assertEqual((await m._meeting(conn, (1,3), 7))['owner_ids'], [3])

if __name__ == '__main__': unittest.main()
