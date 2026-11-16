#!/usr/bin/env python3
"""Read-only verification of deployed connection draft files and storage."""
import argparse, asyncio, hashlib, json
from pathlib import Path
import asyncpg
from dotenv import dotenv_values
ROOT=Path('/opt/callbot-astra')
FILES={'crm/web.py','crm/personal_connections.py','crm/static/personal.js','crm/static/index.html','migrations/049_crm_personal_connections.sql','scripts/crm_setup.sh'}
async def check():
    dsn=dotenv_values(ROOT/'.env').get('DATABASE_URL')
    if not dsn: raise RuntimeError('Database configuration missing')
    conn=await asyncpg.connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            if await conn.fetchval('SELECT current_database()') != 'callbot': raise RuntimeError('Unexpected database')
            if not await conn.fetchval("SELECT to_regclass('public.crm_personal_connections') IS NOT NULL"): raise RuntimeError('Missing table')
            for privilege in ('SELECT','INSERT','UPDATE'):
                if not await conn.fetchval("SELECT has_table_privilege(current_user,'public.crm_personal_connections',$1)", privilege): raise RuntimeError(privilege+' unavailable')
            count=await conn.fetchval('SELECT count(*) FROM crm_personal_connections')
            print(f'Connection drafts: {count}')
    finally: await conn.close()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--manifest-sha256',required=True);args=parser.parse_args()
    raw=args.manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.manifest_sha256.lower(): raise RuntimeError('Manifest hash mismatch')
    manifest=json.loads(raw)
    if set(manifest)!=FILES: raise RuntimeError('Manifest file list mismatch')
    for name in FILES:
        file=ROOT/name
        if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=manifest[name]: raise RuntimeError('File mismatch: '+name)
    print('Package files verified:',len(FILES));asyncio.run(check())
if __name__=='__main__': main()
