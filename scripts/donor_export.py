"""Выгрузка прогонов агента доноров в JSON для разбора (только чтение). Запускается без выкладки:
  cd /opt/callbot-astra && git -c safe.directory=/opt/callbot-astra fetch private-backup && \
  git -c safe.directory=/opt/callbot-astra show private-backup/claude/project-thread-y0tou2:scripts/donor_export.py | .venv/bin/python - банкрот
Берёт последние прогоны, где ниша содержит слово (по умолчанию все, не больше 5), пишет /tmp/donor-runs.json
и печатает короткую сводку. Файл забрать: scp root@сервер:/tmp/donor-runs.json .
"""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import dotenv_values

import asyncpg

OUT = Path('/tmp/donor-runs.json')


async def main(word: str) -> None:
    dsn = dotenv_values(Path.cwd() / '.env').get('DATABASE_URL')
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        """SELECT id, niche, city, offer, project_client, target, status, stage, error, rub, searches, sites_model,
                  created_at, started_at, finished_at, plan, result, log, state->'cost' AS cost,
                  state->'crawl'->'queries' AS queries, jsonb_array_length(coalesce(state->'cands', '[]'::jsonb)) AS cands
           FROM donor_runs WHERE deleted_at IS NULL AND niche ILIKE $1 ORDER BY id DESC LIMIT 5""", f'%{word}%')
    await conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ('plan', 'result', 'log', 'cost', 'queries'):
            d[k] = json.loads(d[k]) if d[k] else None
        for k in ('created_at', 'started_at', 'finished_at'):
            d[k] = d[k].isoformat() if d[k] else None
        d['rub'] = float(d['rub'])
        out.append(d)
        res = d['result'] or {}
        mins = (r['finished_at'] - r['started_at']).total_seconds() / 60 if r['finished_at'] and r['started_at'] else 0
        print(f"#{d['id']} {d['niche']} | {d['city'] or 'вся Россия'} | модель сайтов: {d['sites_model'] or 'dreamn'} | {d['status']} | "
              f"{d['rub']:.0f} ₽ | поисков {d['searches']} | {mins:.0f} мин | в парсинг {res.get('good')} | живых {res.get('live')} | "
              f"номеров {len(res.get('phones') or [])} | компаний {len(res.get('sites') or [])} | расход {d['cost']}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f'Записано {len(out)} прогонов в {OUT}')


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ''))
