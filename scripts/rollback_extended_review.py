"""Explicit rollback, dry-run by default. Never run as part of a button click.

Inspect this file before use. --apply archives ONLY derived overview caches,
unwraps their previous display text (or clears an absent legacy cache), then
restores the five original source/test files. Scores and transcripts stay put.
--restart restarts only callbot-astra.service, and requires --apply.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import subprocess

from unified.overview_cache import rollback_text

PROJECT = Path('/opt/callbot-astra')
FILES = ('unified/render.py','unified/store.py','unified/ui.py','tests/test_unified.py','tests/test_report_reanalysis.py')
PREFIX = '{"_format": "averon.overview.cache.v1"'


async def run(args):
    if socket.gethostname().split('.')[0] != '9015421-nt422325':
        raise ValueError('Wrong server; rollback refused')
    manifest = json.loads((args.backup/'rollback.json').read_text())
    if set(manifest['files']) != set(FILES) or not all((args.backup/name).is_file() for name in FILES):
        raise ValueError('Incomplete rollback point')
    from dotenv import dotenv_values
    import asyncpg
    settings=dotenv_values(PROJECT/'.env')
    conn=await asyncpg.connect(settings['DATABASE_URL'])
    stopped=False
    current={name:(PROJECT/name).read_bytes() for name in FILES} if args.apply else {}
    try:
        if args.apply:
            if args.restart:
                subprocess.run(['systemctl','stop','callbot-astra.service'],check=True)
                stopped=True
            else:
                state=subprocess.check_output(['systemctl','show','callbot-astra.service','-p','ActiveState','--value'],text=True).strip()
                if state not in {'inactive','failed'}:
                    raise ValueError('Stop only the main bot first, or use --restart, to avoid cache races')
        async with conn.transaction(readonly=not args.apply):
            sql='SELECT id,expanded FROM call_report_runs WHERE left(expanded,length($1))=$1'
            if args.apply: sql+=' FOR UPDATE'
            rows=await conn.fetch(sql,PREFIX)
            changed=[(row['id'],row['expanded'],replacement) for row in rows
                     for eligible,replacement in [rollback_text(row['expanded'])] if eligible]
            print(f'Derived overview caches: {len(changed)}; original call analyses stay unchanged.')
            if args.apply and changed:
                archive=args.backup/('overview-caches-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.jsonl')
                with archive.open('x',encoding='utf-8') as handle:
                    archive.chmod(0o600)
                    for run_id,raw,_ in changed:
                        handle.write(json.dumps({'run_id':run_id,'expanded':raw},ensure_ascii=False)+'\n')
                for run_id,_,replacement in changed:
                    await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1',run_id,replacement)
            if args.apply:
                # Restore while the main bot is quiescent and DB changes are
                # still transactional. A copy failure rolls the caches back.
                try:
                    for name in FILES:
                        shutil.copy2(args.backup/name,PROJECT/name)
                except Exception:
                    for name,content in current.items():
                        (PROJECT/name).write_bytes(content)
                    raise
    finally:
        await conn.close()
        if stopped:
            subprocess.run(['systemctl','start','callbot-astra.service'],check=True)
    if args.apply:
        print('Original implementation restored.')
    else:
        print('Dry run only. No files, database rows or services changed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup',type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--restart',action='store_true')
    args=parser.parse_args()
    if args.restart and not args.apply: parser.error('--restart requires --apply')
    asyncio.run(run(args))
