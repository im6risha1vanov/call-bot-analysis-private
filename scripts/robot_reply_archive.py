"""Preview or import already-transcribed packaging calls; no dialing or approvals.

python -m scripts.robot_reply_archive --owner '' --project-id 1 --client-id 1 --olga-extension 14
Add --apply only after reviewing the dry-run counts.
"""
import argparse
import asyncio
import json
import os
import asyncpg
from crm import robot_reply_archive

async def main():
    p=argparse.ArgumentParser()
    p.add_argument('--owner',required=True)
    p.add_argument('--project-id',type=int,required=True)
    p.add_argument('--client-id',type=int,required=True)
    p.add_argument('--olga-extension')
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    pool=await asyncpg.create_pool(os.environ['DATABASE_URL'],min_size=1,max_size=3)
    try:
        result=await robot_reply_archive.run(pool,args.owner,args.project_id,args.client_id,args.olga_extension,args.apply)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    finally:
        await pool.close()

if __name__=='__main__': asyncio.run(main())
