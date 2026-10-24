"""Учётные записи CRM для всех сразу: руководители, менеджеры и клиенты колл-центра.

Запуск на сервере: bash scripts/crm_setup.sh accounts. Логины придумываем из имён, пароли случайные
и печатаем один раз в терминал: в базе только хэш. У кого логин уже есть, того пропускаем
(новый пароль — кнопкой в «Доступах»).
"""
from __future__ import annotations

import re

from crm import auth, cabinet
from tools import home_client_id

ROBOT_EXTENSIONS = {'13'}  # добавочный голосового робота: входа в CRM ему не нужно

_LAT = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'i', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f',
                 'kh', 'ts', 'ch', 'sh', 'shch', '', 'y', '', 'e', 'yu', 'ya']))


def slug(name: str) -> str:
    """«Ольга Просвирова» → olga.prosvirova."""
    text = ''.join(_LAT.get(ch, ch) for ch in str(name or '').lower())
    words = re.findall(r'[a-z0-9]+', text)
    return '.'.join(words)[:36].strip('.')


async def free_login(pool, base: str, taken: set[str] | None = None) -> str:
    """Свободный логин от base: olga.prosvirova, занят — olga.prosvirova2."""
    taken = set() if taken is None else taken
    base = base if len(base) >= 3 else (base + '.crm').strip('.')
    login, n = base, 1
    while login in taken or not await auth.login_free(pool, login):
        n += 1
        login = f'{base}{n}'
    taken.add(login)
    return login


async def create_all(pool) -> list[dict]:
    client_id = await home_client_id(pool)  # клиенты колл-центра пока только у нас
    have = await pool.fetch('SELECT role, extension, client_key, telegram_user_id FROM crm_invites '
                            'WHERE client_id=$1 AND login IS NOT NULL AND revoked_at IS NULL', client_id)
    done_tg = {r['telegram_user_id'] for r in have if r['telegram_user_id']}
    done_ext = {r['extension'] for r in have if r['role'] == 'manager'}
    done_client = {r['client_key'] for r in have if r['role'] == 'client'}
    people = []
    # руководители и владелец: полный доступ директора, со своим Telegram — ещё и личные промты
    for r in await pool.fetch("""SELECT full_name, telegram_user_id FROM employees WHERE client_id=$1 AND role IN ('owner','head')
                                 ORDER BY role DESC, full_name""", client_id):
        if r['telegram_user_id'] not in done_tg:
            tg = str(r['telegram_user_id']) if r['telegram_user_id'] and str(r['telegram_user_id']) in \
                {d['tg'] for d in await auth.directors(pool, client_id)} else None
            people.append(('director', r['full_name'] or 'director', {'tg': tg, 'name': r['full_name']}))
    for m in await pool.fetch("""SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager'
                                 AND extension ~ '^[0-9]+$' ORDER BY extension::int""", client_id):
        if m['extension'] not in done_ext and m['extension'] not in ROBOT_EXTENSIONS:
            people.append(('manager', m['full_name'] or f"ext{m['extension']}", {'extension': m['extension']}))
    try:
        clients = [c['name'] for c in (await cabinet.summary())['clients']]
    except Exception as exc:  # таблица колл-центра недоступна: сотрудников всё равно заводим
        print('clients skipped:', exc)
        clients = []
    for name in clients:
        if name not in done_client:
            people.append(('client', name, {'client_key': name}))
    out, taken = [], set()
    for role, name, extra in people:
        login = await free_login(pool, slug(name) or role, taken)
        password = await auth.account_create(pool, client_id, role, login, extra.get('extension'), extra.get('client_key'),
                                             extra.get('name'), extra.get('tg'))
        out.append({'role': role, 'name': name, 'login': login, 'password': password})
    return out
