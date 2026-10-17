"""Проверка звонка из CRM через Mango (commands/callback).

Запуск на сервере из /opt/callbot-astra:
    sudo -u callbot-astra .venv/bin/python scripts/mango_call_test.py probe
        — ничего не звонит: шлёт команду на несуществующий добавочный 999 и печатает
          ответ Mango. По коду видно, разрешены ли нашему ключу команды звонка.
    sudo -u callbot-astra .venv/bin/python scripts/mango_call_test.py call EXT NUMBER
        — настоящий звонок: Mango звонит на добавочный EXT, после ответа набирает NUMBER.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


async def main(argv: list[str]) -> int:
    load_env(ROOT / '.env')
    import asyncpg
    import mango_client
    from crypto_util import decrypt
    if not argv or argv[0] not in ('probe', 'call') or (argv[0] == 'call' and len(argv) != 3):
        print(__doc__)
        return 2
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=1)
    client = await pool.fetchrow('SELECT * FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    key, salt = decrypt(client['vpbx_api_key_enc']), decrypt(client['vpbx_api_salt_enc'])
    ext, number = ('999', '70000000000') if argv[0] == 'probe' else (argv[1], ''.join(ch for ch in argv[2] if ch.isdigit()))
    try:
        res = await mango_client.callback(key, salt, ext, number, f'crm-test-{int(time.time())}')
        print('Mango:', res)
    except Exception as e:  # noqa: BLE001 — нужен текст ответа Mango как есть
        print('Mango error:', e)
    finally:
        await mango_client.close()
        await pool.close()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main(sys.argv[1:])))
