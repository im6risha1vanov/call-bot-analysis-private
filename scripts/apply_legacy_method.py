"""Deploy legacy display/policy, then send one saved owner-only preview.

Default: read-only local provenance/backup check. --apply: schema backup,
additive migration 007, restart main/worker/queue only, select the legacy
package for the verified owner's client, deliver saved short and expanded
reports. No models, ASR, historical rescore, trainer restart or general mail.
Requires a normal authorized server session with systemd and .env access.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path('/opt/callbot-astra')
UNITS={'callbot-astra.service':'demo_bot.py',
       'callbot-astra-worker.service':'astra_worker.py',
       'callbot-astra-queue-runner.service':'run_queue_runner.py'}
TRAINER='callbot-astra-trainer.service'
SOURCE='preview:legacy-method:20261007-v1'

def original_backup():
    from unified.analysis_policy import BACKUP
    folder=Path(BACKUP);data=json.loads((folder/'backup.json').read_text())
    digest=hashlib.sha256((folder/'current-source.tar.gz').read_bytes()).hexdigest()
    if digest!=data['archive_sha256']:raise ValueError('Original backup checksum mismatch')
    if data['files']<100:raise ValueError('Incomplete original backup')
    return folder

def state(unit):
    return subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip()

def preflight_units():
    for unit,entry in UNITS.items():
        directory=subprocess.check_output(['systemctl','show',unit,'-p','WorkingDirectory','--value'],text=True).strip()
        command=subprocess.check_output(['systemctl','show',unit,'-p','ExecStart','--value'],text=True)
        if directory!=str(ROOT) or str(ROOT/entry) not in command:raise ValueError('Unexpected unit entrypoint')
        if state(unit)!='active':raise ValueError('Required Astra unit not active')

def restart():
    before=state(TRAINER)
    started=str(int(datetime.now(timezone.utc).timestamp()))
    for unit in UNITS:
        subprocess.run(['systemctl','restart',unit],check=True)
        if state(unit)!='active':raise RuntimeError('Restart failed')
    time.sleep(3)
    if any(state(unit)!='active' for unit in UNITS):raise RuntimeError('A restarted service failed during startup')
    if state(TRAINER)!=before:raise RuntimeError('Trainer state changed')
    for unit in UNITS:
        recent=subprocess.check_output(['journalctl','-u',unit,'--since','@'+started,
                                      '-n','80','--no-pager','-o','json'],text=True)
        for line in recent.splitlines():
            message=str(json.loads(line).get('MESSAGE',''))
            if any(s in message for s in ['Traceback (most recent call last)',' ERROR ',' CRITICAL ','SyntaxError:','ModuleNotFoundError:']):
                raise RuntimeError('Startup errors; inspect locally before selecting legacy')

async def preview(pool, actor):
    from scripts.deploy_legacy_preview import LATEST
    from unified import store,analysis_policy
    from tools import Forbidden
    if actor is None or actor.role!='owner':raise Forbidden('Owner-only preview')
    async with pool.acquire() as conn,conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',SOURCE+':'+str(actor.client_id))
        run_id=await conn.fetchval('SELECT id FROM call_report_runs WHERE client_id=$1 AND source_key=$2 ORDER BY id LIMIT 1',actor.client_id,SOURCE)
        if run_id is None:
            call_id=await conn.fetchval(LATEST,actor.client_id)
            if call_id is None:raise ValueError('No suitable saved call')
            run_id=await store.capture(conn,call_id,actor.client_id,SOURCE,fresh=False,
                                       package=analysis_policy.legacy_configuration())
    row=await store.access(pool,actor,run_id)
    if row['configuration'].get('display_version')!=analysis_policy.VERSION:
        raise ValueError('Unexpected saved preview version')
    return await store.prepare(pool,run_id,wait=False)

async def apply():
    # Access preflight occurs before migrations/restart/send. Secret values
    # are used internally and never printed or placed in backup documents.
    preflight_units()
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    import asyncpg
    from aiogram import Bot
    from scripts.deploy_legacy_preview import recipient
    from unified import analysis_policy,store,ui,overview_cache
    folder=original_backup()
    pool=await asyncpg.create_pool(os.environ['DATABASE_URL'])
    bot=None
    try:
        actor=await recipient(pool)
        if not await store.installed(pool):raise ValueError('Existing unified schema is required')
        from scripts.deploy_legacy_preview import LATEST
        if await pool.fetchval(LATEST,actor.client_id) is None:raise ValueError('No suitable saved preview call')
        point=folder/('deployment-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        point.mkdir(mode=0o700)
        # Schema only. Never use DB URL as a command argument/process listing.
        env={**os.environ,'PGDATABASE':os.environ['DATABASE_URL']}
        subprocess.run(['pg_dump','--schema-only','--no-owner','--no-privileges','--file',str(point/'schema.sql')],env=env,check=True,capture_output=True)
        (point/'schema.sql').chmod(0o600)
        if await analysis_policy.installed(pool):
            rows=await pool.fetch('SELECT * FROM call_analysis_policy WHERE client_id=$1',actor.client_id)
            (point/'policy-before.json').write_text(json.dumps([dict(r) for r in rows],default=str,indent=2))
            (point/'policy-before.json').chmod(0o600)
        # Hold table locks across the idle check/restart: a new claim cannot
        # race the check and start a paid request immediately before restart.
        # Claims blocked on UPDATE have not committed and cannot bill yet.
        async with pool.acquire() as conn,conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='3s'")
            await conn.execute('LOCK TABLE tasks,call_report_generation,call_report_runs IN SHARE MODE')
            if await conn.fetchval("SELECT count(*) FROM tasks WHERE status='processing'"):
                raise ValueError('Queue busy: rerun after current tasks finish')
            if await conn.fetchval("SELECT count(*) FROM call_report_generation WHERE status='processing'"):
                raise ValueError('An explicit paid request is still processing')
            await conn.execute((ROOT/'migrations/007_call_analysis_policy.sql').read_text())
            restart()
        cfg=await analysis_policy.switch(pool,actor,'legacy')
        print('Active for verified owner client:',cfg['methodology_version'],cfg['display_version'])
        row=await preview(pool,actor)
        bot=Bot(os.environ['BOT_TOKEN'])
        await store.deliver(pool,bot,row,only_to=actor.telegram_user_id)
        status=await pool.fetchval('SELECT status FROM call_report_delivery WHERE run_id=$1 AND recipient=$2',row['id'],actor.telegram_user_id)
        if status!='sent':raise RuntimeError('Short report delivery is not confirmed; no blind retry')
        await ui.show(bot,pool,actor,row['id'],detailed=True)
        receipt=await overview_cache.delivered_view(pool,actor,row['id'])
        if not receipt:raise RuntimeError('Expanded delivery is not confirmed; no blind retry')
        print('Both previews delivered only to verified owner; run:',row['id'])
        print('Deployment schema/policy backup:',point)
    finally:
        if bot:await bot.session.close()
        await pool.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if socket.gethostname().split('.')[0]!='9015421-nt422325' or Path.cwd().resolve()!=ROOT:
        raise ValueError('Wrong server/project')
    sys.path.insert(0,str(ROOT))
    from unified.analysis_policy import verify_original
    verify_original();print('Verified original backup:',original_backup())
    if not args.apply:
        print('Read-only: deployment is prepared, not applied. No DB, model, message or restart.')
        return
    # Checks precede every mutation. They use synthetic env only.
    subprocess.run([sys.executable,'-B',str(ROOT/'scripts/check_legacy_rollback.py')],check=True,cwd=ROOT)
    asyncio.run(apply())

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Legacy deployment did not complete:',type(exc).__name__,file=sys.stderr)
        raise SystemExit(1)
