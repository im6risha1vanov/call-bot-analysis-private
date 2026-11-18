"""Bind an existing project to the packaging operator; never creates a campaign or calls.
python -m scripts.bind_packaging_operator --owner '' --project-id 123
"""
import argparse
import asyncio
import os
import asyncpg
from crm import robot_store
from voice.operator import load


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', required=True)
    parser.add_argument('--project-id', required=True, type=int)
    args = parser.parse_args()
    robot_store._owned('packaging', args.owner)
    load('packaging')
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow('SELECT id,operator FROM robot_reply_projects WHERE id=$1 AND owner=$2 FOR UPDATE', args.project_id, args.owner)
            if not row:
                raise ValueError('Проект не найден в этом кабинете')
            if row['operator'] not in (None, '', 'packaging'):
                raise ValueError('В проекте уже выбран другой оператор; автоматическая замена запрещена')
            await conn.execute("UPDATE robot_reply_projects SET operator='packaging' WHERE id=$1 AND owner=$2", args.project_id, args.owner)
            print(f"project_id={row['id']} operator=packaging")
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
