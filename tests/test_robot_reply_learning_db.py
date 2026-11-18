import asyncio, asyncpg, json, os, uuid
from pathlib import Path
from crm import robot_reply_learning as x

async def main():
    dsn=os.environ['METHODOLOGY_TEST_DATABASE_URL']
    admin=await asyncpg.connect(dsn)
    schema='reply_test_'+uuid.uuid4().hex
    await admin.execute('CREATE SCHEMA '+schema)
    pool=await asyncpg.create_pool(dsn,server_settings={'search_path':schema})
    await pool.execute('CREATE TABLE robot_campaigns(id BIGSERIAL PRIMARY KEY,owner TEXT,operator TEXT)')
    await pool.execute('CREATE TABLE calls(id BIGSERIAL PRIMARY KEY,client_id INTEGER,transcript TEXT)')
    await pool.execute('CREATE TABLE robot_calls(call_id TEXT PRIMARY KEY,owner TEXT,campaign_id BIGINT,operator TEXT,outcome TEXT,data JSONB,at TIMESTAMPTZ DEFAULT now())')
    await pool.execute('CREATE TABLE call_analysis_runs(id BIGSERIAL PRIMARY KEY,client_id INTEGER,operator TEXT)')
    await pool.execute('CREATE TABLE call_analysis_items(id BIGSERIAL PRIMARY KEY,run_id BIGINT,client_id INTEGER,call_id BIGINT,stages JSONB)')
    for filename in ('051_robot_reply_library.sql','052_robot_reply_learning.sql'):
        await pool.execute(Path('migrations',filename).read_text(encoding='utf-8-sig'))
    await pool.execute("INSERT INTO robot_reply_projects(id,owner,name,profile) VALUES(1,'','packaging','packaging'),(2,'other','other','generic')")
    await pool.execute("INSERT INTO robot_reply_learning_scripts(owner,project_id,client_id,script,verified_by) VALUES('',1,1,'packaging','test')")
    await pool.execute("INSERT INTO call_analysis_runs(id,client_id,operator) VALUES(1,1,'oksana')")
    for i,q in ((1,'Какая цена?'),(2,'Сколько стоит?')):
        t=f'[00:01 Спикер 1] Здравствуйте, меня зовут Оксана.\n[00:04 Спикер 0] {q}\n[00:08 Спикер 1] Уточним.'
        await pool.execute('INSERT INTO calls(id,client_id,transcript) VALUES($1,1,$2)',i,t)
        await pool.execute("INSERT INTO call_analysis_items(run_id,client_id,call_id,stages) VALUES(1,1,$1,'{\"script_check\":{\"script\":\"packaging\"}}')",i)
    assert await x.backfill(pool,only_new=True)==(0,2)
    assert await x.backfill(pool,only_new=True)==(0,0)
    assert await x.propose(pool,'',1)==1
    assert await x.propose(pool,'',1)==0
    assert await pool.fetchval('SELECT count(*) FROM robot_reply_candidates')==1
    assert await pool.fetchval('SELECT count(*) FROM robot_reply_learning_links')==2
    assert not await x.evidence_for_candidate(pool,'other',2,1)
    await pool.execute("UPDATE robot_reply_candidates SET status='rejected' WHERE id=1")
    await pool.execute("UPDATE calls SET transcript=replace(transcript,'Какая цена?','Какая цена, примерно?') WHERE id=1")
    await x.backfill(pool)
    await x.propose(pool,'',1)
    row=await pool.fetchrow('SELECT status,current_version FROM robot_reply_candidates WHERE id=1')
    assert (row['status'],row['current_version'])==('rejected',1),row
    print('postgres integration ok:', json.dumps(await x.coverage(pool,'',1),ensure_ascii=False))
    await pool.close()
    await admin.execute('DROP SCHEMA '+schema+' CASCADE')
    await admin.close()
if __name__ == '__main__':
    asyncio.run(main())
