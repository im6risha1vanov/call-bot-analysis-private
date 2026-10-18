"""Задним числом разбирает звонки оператора прозвона (KITCHEN_EXTENSIONS) и обновляет его листы.

Запуск на сервере из /opt/callbot-astra:
    sudo -u callbot-astra .venv/bin/python scripts/kitchen_backfill.py [YYYY-MM-DD] [--dry-run]
Без даты берётся начало сегодняшнего дня по часовому поясу клиента. --dry-run только
считает звонки и ничего не разбирает.

Берёт исходящие звонки с этого момента, по которым ещё нет попытки в lead_attempts:
без разговора пишет НДЗ, разговор разбирает промтом кухонь (готовая расшифровка
из calls или запись из Mango и распознавание). Статусы звонков, разборы Averon
и сообщения в Telegram не трогает. В конце пересобирает листы оператора.
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


def load_env(path: Path) -> None:
    """Значения из .env для запуска руками; уже заданные переменные не меняются."""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


log = logging.getLogger('kitchen-backfill')


async def mango_transcript(pool, call) -> str:
    import mango_client
    from crypto_util import decrypt
    from deepgram_client import transcribe_bytes
    client = await pool.fetchrow('SELECT * FROM clients WHERE id=$1', call['client_id'])
    audio = await mango_client.fetch_recording(decrypt(client['vpbx_api_key_enc']), decrypt(client['vpbx_api_salt_enc']),
                                               call['recording_id'])
    transcript, _ = await transcribe_bytes(audio)
    return transcript


async def backfill(pool, since: datetime, transcribe=mango_transcript, run=None, dry_run=False) -> dict:
    from review import kitchen
    run = run or kitchen.run
    calls = await pool.fetch(
        """SELECT c.*, coalesce((c.raw_summary::jsonb->>'talk_duration')::numeric, 0) AS talk FROM calls c
           WHERE c.extension = ANY($1::text[]) AND c.call_started_at >= $2
             AND (c.raw_summary::jsonb->>'context_type') = '2'
             AND NOT EXISTS (SELECT 1 FROM lead_attempts a WHERE a.call_id=c.id)
           ORDER BY c.call_started_at""", sorted(kitchen.extensions()), since)
    done = {'ndz': 0, 'talks': 0, 'failed': 0}
    if dry_run:
        talks = sum(1 for c in calls if c['talk'] > 0)
        return {'ndz': len(calls) - talks, 'talks': talks, 'failed': 0}
    for call in calls:
        talk = call['talk']
        try:
            if talk == 0:
                data, cost = kitchen.ndz('Не дозвонился'), 0.0
                done['ndz'] += 1
            else:
                transcript = call['transcript'] or ''
                if not transcript.strip() and call['recording_id']:
                    transcript = await transcribe(pool, call)
                data, cost = await run(transcript)
                done['talks'] += 1
            async with pool.acquire() as conn:
                await kitchen.record(conn, call, data, cost)
            log.info('звонок %s %s: %s', call['id'], call['call_started_at'], data['status'])
        except Exception:
            done['failed'] += 1
            log.exception('звонок %s не разобран', call['id'])
    return done


async def main() -> None:
    load_env(ROOT / '.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    import asyncpg
    from review import kitchen
    if not kitchen.extensions():
        sys.exit('KITCHEN_EXTENSIONS не задан')
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=3)
    try:
        zone = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients ORDER BY id LIMIT 1') or 'Europe/Moscow')
        args = [a for a in sys.argv[1:] if a != '--dry-run']
        dry_run = '--dry-run' in sys.argv
        day = datetime.strptime(args[0], '%Y-%m-%d').date() if args else datetime.now(zone).date()
        since = datetime.combine(day, datetime.min.time(), zone)
        done = await backfill(pool, since, dry_run=dry_run)
        print(f"{'Найдено' if dry_run else 'Записано'} с {since:%d.%m.%Y %H:%M}: недозвонов {done['ndz']}, "
              f"разговоров {done['talks']}, ошибок {done['failed']}")
        if kitchen.sheet_enabled() and not dry_run:
            print('Строк в таблице (попыток):', await kitchen.sync(pool))
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
