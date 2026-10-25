"""Проверка помощника «Автоматизаций» на сервере: типовые просьбы директора, ответы и черновики в терминал.

Ничего не сохраняет и не включает (помощник только собирает черновик). По умолчанию YandexGPT. Запуск:
  bash scripts/crm_setup.sh auto-agent-check            # YandexGPT
  bash scripts/crm_setup.sh auto-agent-check dreamn     # для сравнения dreamn
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / '.env')
KIND = (sys.argv[1] if len(sys.argv) > 1 else 'yandex').strip()
os.environ['AVERONGPT_MODELS'] = {'yandex': 'yandex:yandexgpt-5.1', 'dreamn': 'dreamn:gpt-6.1-sol'}.get(KIND, KIND)

import asyncpg  # noqa: E402

from crm import automation_agent  # noqa: E402

ASKS = [
    'Присылай в Telegram каждую оплату больше 100 000 ₽',
    'Каждый вечер в 19:00 итоги дня в чат',
    'Записывай новые номера из базы в Google-таблицу',
    'Когда менеджер назначил встречу, напиши в чат кто, с кем и когда',
    'Хочу, чтобы оплаты попадали в Notion',
    'Отправляй отказы на почту boss@example.ru с причиной',
    'Сделай что-нибудь полезное',
]


async def main():
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=3)
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    for text in ASKS:
        t0 = time.monotonic()
        r = await automation_agent.ask(pool, client_id, text)
        print(f'\n=== {text}  ({time.monotonic() - t0:.1f} с)\n{r["reply"]}')
        if r.get('draft'):
            print('Черновик:', r['draft']['name'], '|', r.get('text', ''))
            print(json.dumps(r['draft']['spec'], ensure_ascii=False))
            if r.get('missing'):
                print('Нужно подключить:', ', '.join(r['missing']))
    await pool.close()


asyncio.run(main())
