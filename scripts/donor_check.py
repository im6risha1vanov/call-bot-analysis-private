"""Проверка агента доноров на сервере: ключи, один поиск Яндекса, чтение одной страницы и короткий вопрос каждой модели.

Ничего не пишет в базу. Поиск стоит около 0,5 ₽, вопросы моделям — доли цента. Запуск:
  bash scripts/crm_setup.sh donor-check
С аргументом deferred проверяет только отложенный поиск: один запрос, ждёт до 15 минут и печатает,
что ответил Яндекс (около 0,03 ₽).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / '.env')

import httpx  # noqa: E402

from crm import donor_agent as agent, donor_web as webx  # noqa: E402


async def deferred() -> None:
    async with httpx.AsyncClient() as http:
        op = await webx.call(http, 'POST', webx.ASYNC_URL, webx.search_body('кухни на заказ краснодар', webx.region_of('Краснодар'), 10))
        print('Отправлено:', {k: v for k, v in op.items() if k != 'response'})
        if not op.get('id'):
            return
        for i in range(45):
            await asyncio.sleep(20)
            op = await webx.call(http, 'GET', webx.OPERATION_URL + op['id'])
            if op.get('done'):
                raw = (op.get('response') or {}).get('rawData') or ''
                print(f'Готово через {(i + 1) * 20} с:', {k: v for k, v in op.items() if k != 'response'},
                      'ключи ответа:', list((op.get('response') or {}).keys()), 'длина rawData:', len(raw))
                if raw:
                    res = webx.decode(raw)
                    print(f'Результатов: {len(res)}:', ', '.join(r['host'] for r in res[:8]))
                return
            print(f'{(i + 1) * 20} с: ещё не готово')
        print('За 15 минут ответа нет.')


async def main() -> None:
    print('Каталог Яндекса:', webx.folder() or 'НЕТ (YANDEX_FOLDER_ID)')
    print('Ключ поиска:', 'есть' if webx.search_key() else 'НЕТ (YANDEX_SEARCH_API_KEY)')
    print('Модели:', ', '.join(f"{p['name']} {p['model']}" for p in agent.providers()) or 'НЕТ ключей')
    print('Лимит: в день %g ₽, на прогон %g ₽' % (agent.daily_limit(), agent.run_limit()))
    async with httpx.AsyncClient() as http:
        if webx.search_ready():
            try:
                res = await webx.search(http, 'кухни на заказ краснодар', webx.region_of('Краснодар'), 10)
                print(f'Поиск: {len(res)} результатов:', ', '.join(r['host'] for r in res[:8]))
                site = next((r for r in res if not webx.AGG.search(r['host'])), None)
                if site:
                    page = await webx.open_page(http, site['url'])
                    print(f'Страница {page["url"]}: {page["status"]}, номеров {len(page["phones"])}, технологии: {", ".join(page["tech"]) or "нет"}')
            except Exception as e:  # noqa: BLE001
                print('Поиск НЕ работает:', e)
        for p in agent.providers():
            try:
                resp = await agent.ai_client(p).messages.create(model=p['model'], max_tokens=50, system='Отвечай одним словом.',
                                                                messages=[{'role': 'user', 'content': 'Столица России?'}])
                print(f'{p["name"]}: {"".join(getattr(b, "text", "") for b in resp.content).strip()[:60]}')
            except Exception as e:  # noqa: BLE001
                print(f'{p["name"]} НЕ отвечает: {type(e).__name__}: {str(e)[:150]}')


asyncio.run(deferred() if sys.argv[1:] == ['deferred'] else main())
