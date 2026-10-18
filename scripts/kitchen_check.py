"""Проверка: находит ли бот номера прозвона в листе базы. Только чтение.

Запуск на сервере из /opt/callbot-astra:
    runuser -u callbot-astra -- .venv/bin/python scripts/kitchen_check.py [YYYY-MM-DD]
Показывает листы таблицы, заголовки «База от …» в листе базы и сколько номеров
из попыток с этой даты (по умолчанию 08.10.2026) нашлось в листе, а сколько нет.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from kitchen_backfill import load_env  # noqa: E402


async def main(day: date) -> None:
    import asyncpg
    import httpx
    from review import kitchen, sheets
    token = await asyncio.to_thread(sheets.token)
    async with httpx.AsyncClient(timeout=60) as client:
        meta = await client.get(sheets.API + os.environ['KITCHEN_SHEET_ID'], headers={'Authorization': 'Bearer ' + token},
                                params={'fields': 'sheets(properties(title,gridProperties))'})
        meta.raise_for_status()
        print('Листы таблицы:')
        for sheet in meta.json()['sheets']:
            props = sheet['properties']
            print(f"  {props['title']}: строк {props['gridProperties']['rowCount']}")
        raw = await client.get(sheets.API + os.environ['KITCHEN_SHEET_ID'] + f"/values/'{kitchen.LEADS_TAB}'!A1:Z",
                               headers={'Authorization': 'Bearer ' + token}, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        raw.raise_for_status()
    rows = raw.json().get('values', [])
    print(f'\nЛист «{kitchen.LEADS_TAB}»: строк с данными {len(rows)}')
    print('  Шапка:', rows[0] if rows else '—')
    for row in rows[1:]:
        if row and 'база' in str(row[0]).lower():
            print('  Заголовок:', row[:6])
    print('  Последние 5 строк:')
    for row in rows[-5:]:
        print('   ', row[:8])

    leads = await kitchen.base(access_token=token)
    bases = {}
    for lead in leads.values():
        bases[lead['base'] or 'без заголовка'] = bases.get(lead['base'] or 'без заголовка', 0) + 1
    print('\nНомеров по базам:', bases)

    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
    try:
        attempts = await pool.fetch('SELECT client_number, call_started_at, status FROM lead_attempts WHERE call_started_at >= $1 '
                                    'ORDER BY call_started_at', datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
    finally:
        await pool.close()
    numbers = {kitchen.digits(a['client_number']) for a in attempts}
    missing = sorted(n for n in numbers if n not in leads)
    print(f'\nПопыток с {day:%d.%m.%Y}: {len(attempts)}, номеров {len(numbers)}, найдено в листе {len(numbers) - len(missing)}, '
          f'нет в листе {len(missing)}')
    print('  Примеры номеров, которых нет:', ', '.join('+7' + n for n in missing[:10]) or '—')


if __name__ == '__main__':
    load_env(ROOT / '.env')
    asyncio.run(main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 10, 8)))
