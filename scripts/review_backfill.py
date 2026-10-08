"""Задним числом делает разбор по результату (v2) для звонков, разобранных до его включения.

Запуск на сервере из /opt/callbot-astra:
    runuser -u callbot-astra -- .venv/bin/python scripts/review_backfill.py [YYYY-MM-DD] [--dry-run]
Без даты берётся начало сегодняшнего дня по часовому поясу клиента.

Берёт звонки менеджеров с этого момента, у которых уже есть расшифровка, но нет
записи в call_reviews (их разобрала старая версия). Разбирает расшифровку заново,
сохраняет разбор и перезвоны. В Telegram ничего не отправляет: разборы сразу
помечаются отправленными, менеджеры свои разборы по этим звонкам уже получили.
Оператор прозвона (KITCHEN_EXTENSIONS) сюда не входит, для него kitchen_backfill.py.
В конце обновляет общую Google-таблицу.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kitchen_backfill import load_env  # noqa: E402

log = logging.getLogger('review-backfill')


async def backfill(pool, since: datetime, meta=None, run=None, dry_run=False) -> dict:
    from review import callbacks, engine, kitchen, store
    run = run or engine.run
    calls = await pool.fetch(
        """SELECT c.* FROM calls c
           WHERE c.call_started_at >= $1 AND c.status IN ('analyzed','delivered')
             AND coalesce(c.transcript, '') <> '' AND NOT (coalesce(c.extension, '') = ANY($2::text[]))
             AND NOT EXISTS (SELECT 1 FROM call_reviews r WHERE r.call_id=c.id)
           ORDER BY c.call_started_at""", since, sorted(kitchen.extensions()))
    done = {'calls': len(calls), 'reviewed': 0, 'failed': 0}
    if dry_run:
        return done
    for call in calls:
        try:
            client = await pool.fetchrow('SELECT * FROM clients WHERE id=$1', call['client_id'])
            data, cost = await run(call['transcript'], await meta(pool, client, call))
            async with pool.acquire() as conn, conn.transaction():
                await store.save(conn, call, data, cost)
                await conn.execute('UPDATE call_reviews SET manager_sent_at=now(), rop_sent_at=now() WHERE call_id=$1', call['id'])
                if await conn.fetchval("SELECT 1 FROM information_schema.columns WHERE table_name='call_reviews' AND column_name='copy_sent_at'"):
                    await conn.execute('UPDATE call_reviews SET copy_sent_at=now() WHERE call_id=$1', call['id'])
                await callbacks.record(conn, call, data)
            done['reviewed'] += 1
            log.info('звонок %s %s: %s', call['id'], call['call_started_at'], data.get('outcome') or data.get('kind'))
        except Exception:
            done['failed'] += 1
            log.exception('звонок %s не разобран', call['id'])
    return done


async def main() -> None:
    load_env(ROOT / '.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    import asyncpg
    from handlers.analyze_call import _review_meta
    from review import sheets
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=3)
    try:
        zone = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients ORDER BY id LIMIT 1') or 'Europe/Moscow')
        args = [a for a in sys.argv[1:] if a != '--dry-run']
        dry_run = '--dry-run' in sys.argv
        day = datetime.strptime(args[0], '%Y-%m-%d').date() if args else datetime.now(zone).date()
        since = datetime.combine(day, datetime.min.time(), zone)
        done = await backfill(pool, since, _review_meta, dry_run=dry_run)
        print(f"С {since:%d.%m.%Y %H:%M}: звонков без нового разбора {done['calls']}, "
              f"разобрано {done['reviewed']}, ошибок {done['failed']}")
        if sheets.enabled() and not dry_run:
            await sheets.sync(pool)
            print('Общая таблица обновлена')
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
