"""Isolated preview only: requires a dedicated test database, never production."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncpg
from aiohttp import web
from crm import auth
from crm import web as crm_web


async def main():
    dsn = os.environ['METHODOLOGY_TEST_DATABASE_URL']
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    dbname = await pool.fetchval('SELECT current_database()')
    if not dbname.startswith('callbot_methodology_test_personal_'):
        await pool.close()
        raise RuntimeError('Preview requires a dedicated personal test database')
    os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS'] = '90000123,90000124'
    crm_web.SECURE_COOKIE = False
    await pool.execute("INSERT INTO clients(id,timezone,processing_enabled) VALUES(991,'Europe/Moscow',true) ON CONFLICT(id) DO NOTHING")
    for tg, ext, login, name in [(90000123,'901','personalpreview1','Тест Первый'), (90000124,'902','personalpreview2','Тест Второй')]:
        await pool.execute("""INSERT INTO employees(client_id,extension,telegram_user_id,role,full_name)
            VALUES(991,$1,$2,'manager',$3) ON CONFLICT(client_id,extension) DO UPDATE
            SET telegram_user_id=excluded.telegram_user_id,role='manager',full_name=excluded.full_name,archived_at=NULL""", ext, tg, name)
        await pool.execute("""INSERT INTO crm_invites(token_hash,role,client_id,extension,telegram_user_id,login,password_hash,name)
            VALUES($1,'manager',991,$2,$3,$4,$5,$6) ON CONFLICT(token_hash) DO UPDATE
            SET revoked_at=NULL,telegram_user_id=excluded.telegram_user_id,login=excluded.login,password_hash=excluded.password_hash""",
            'preview-invite-'+str(tg), ext, tg, login, auth.hash_password('Preview2026!'), name)
    runner = web.AppRunner(crm_web.build(pool)); await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 8098).start()
    print('Preview on 127.0.0.1:8098; synthetic accounts personalpreview1 / personalpreview2')
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup(); await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
