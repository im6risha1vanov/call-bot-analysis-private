"""Explicit one-time project/script mapping and bounded historical backfill.
Run after migrations: python -m scripts.robot_reply_backfill --project-id ID --owner OWNER --client-id 1 --script packaging --verified-by NAME
DATABASE_URL is read from the environment. Does not start calls or deploy anything.
"""
import argparse
import asyncio
import json
import os

import asyncpg
from crm import robot_reply_learning as learning


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-id', type=int, required=True)
    parser.add_argument('--owner', required=True, help='CRM owner; empty string for director cabinet')
    parser.add_argument('--client-id', type=int, required=True)
    parser.add_argument('--script', required=True)
    parser.add_argument('--verified-by', required=True)
    args = parser.parse_args()
    if not args.verified_by.strip() or not args.script.strip():
        parser.error('script and verified-by are required')
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=3)
    try:
        result = await pool.execute('''INSERT INTO robot_reply_learning_scripts(owner,project_id,client_id,script,verified_by)
            SELECT $1,id,$3,$4,$5 FROM robot_reply_projects WHERE id=$2 AND owner=$1
            ON CONFLICT(owner,project_id,client_id,script) DO NOTHING''', args.owner, args.project_id, args.client_id, args.script, args.verified_by)
        if not await pool.fetchval('SELECT 1 FROM robot_reply_projects WHERE id=$1 AND owner=$2', args.project_id, args.owner):
            raise ValueError('Проект не принадлежит кабинету')
        processed = 0
        while True:
            robots, humans = await learning.backfill(pool, limit=200, only_new=True, owner=args.owner, project_id=args.project_id)
            processed += robots + humans
            if robots < 200 and humans < 200:
                break
        created = await learning.propose(pool, args.owner, args.project_id)
        print(json.dumps({'processed': processed, 'drafts_created': created, 'coverage': await learning.coverage(pool, args.owner, args.project_id)}, ensure_ascii=False))
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
