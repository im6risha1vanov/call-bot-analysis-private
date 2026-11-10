"""Кому доступны фильтры периода и сотрудников в «Колл-центре»: Григорий, Евгений и pivondopola.

Сопоставление не угадывается. Сотрудник ищется среди активных записей ``employees``
с ролью ``owner``/``head``:

* имя — точным словом в ФИО («Григорий Иванов» подходит, «Гриша» — нет);
* добавочный руководителя — ``head:<транслит имени>``, как его строит ``crm/staff.py``;
* pivondopola — точным ником ``telegram_username`` (колонка из миграции 019).

Если подходящих записей нет или их несколько, доступ не выдаётся: в ответе видно,
какое сопоставление не подтверждено. Явный список владельца
``CALLCENTER_FILTER_VIEWERS`` (добавочные, Telegram ID или ники через запятую)
подтверждает доступ без догадок, если имя в базе записано иначе.

Проверка серверная: её вызывает API, а не только интерфейс. Тестовый и служебный
входы фильтры не получают.
"""
from __future__ import annotations

import os
import re

from .staff import head_extension

TARGETS = (
    {'key': 'grigory', 'label': 'Григорий', 'name': 'Григорий'},
    {'key': 'evgeny', 'label': 'Евгений', 'name': 'Евгений'},
    {'key': 'pivondopola', 'label': 'pivondopola', 'username': 'pivondopola'},
)
ENV = 'CALLCENTER_FILTER_VIEWERS'
WHO = 'Григорий, Евгений и pivondopola'


def norm(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '').lower().replace('ё', 'е')).strip()


def env_tokens(env=None) -> list[str]:
    raw = (os.environ if env is None else env).get(ENV) or ''
    return [t.strip().lstrip('@') for t in re.split(r'[,;\s]+', str(raw)) if t.strip().lstrip('@')]


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, TypeError):
        return getattr(row, key, default)


async def employees(pool, client_id: int) -> list:
    """Активные владелец и руководители компании. Нет колонки ника — работаем без неё."""
    query = """SELECT extension, full_name, role, telegram_user_id, telegram_username
               FROM employees WHERE client_id=$1 AND archived_at IS NULL AND role IN ('owner','head')
               ORDER BY extension"""
    try:
        return await pool.fetch(query, client_id)
    except Exception as exc:  # noqa: BLE001 — старая база без telegram_username
        if 'telegram_username' not in str(exc):
            raise
    fallback = """SELECT extension, full_name, role, telegram_user_id, NULL AS telegram_username
                  FROM employees WHERE client_id=$1 AND archived_at IS NULL AND role IN ('owner','head')
                  ORDER BY extension"""
    return await pool.fetch(fallback, client_id)


def token_matches(row, token) -> bool:
    """Ник, Telegram ID или добавочный — по точному значению."""
    token = norm(token).lstrip('@')
    if not token:
        return False
    if token.lstrip('-').isdigit():
        return str(_get(row, 'telegram_user_id') or '') == token
    if norm(_get(row, 'telegram_username')) == token:
        return True
    return norm(_get(row, 'extension')) == token


def name_matches(row, needle: str) -> bool:
    if norm(needle) in norm(_get(row, 'full_name')).replace('.', ' ').split():
        return True
    try:
        return norm(_get(row, 'extension')) == norm('head:' + head_extension(needle))
    except ValueError:
        return False


def resolve(rows, env=None) -> list[dict]:
    """Кто из трёх подтверждён данными, а кто нет — с причиной."""
    pinned = env_tokens(env)
    out = []
    for target in TARGETS:
        if target.get('username'):
            found = [r for r in rows if token_matches(r, target['username'])]
            missing = f'В сотрудниках нет владельца или руководителя с ником {target["label"]}.'
            needs = f'Укажите точный ник в {ENV}.'
        else:
            found = [r for r in rows if name_matches(r, target['name'])]
            missing = f'В сотрудниках нет активного владельца или руководителя с именем {target["label"]}.'
            needs = f'Укажите точный добавочный, ник или Telegram ID в {ENV}.'
        item = {'key': target['key'], 'label': target['label'], 'confirmed': False,
                'extension': None, 'telegram_user_id': None, 'name': '', 'role': '', 'reason': ''}
        if len(found) == 1:
            row = found[0]
            item.update(confirmed=True, extension=_get(row, 'extension'), name=_get(row, 'full_name') or '',
                        role=_get(row, 'role') or '', telegram_user_id=_get(row, 'telegram_user_id'))
        elif not found:
            item['reason'] = f'{missing} {needs}'
        else:
            people = ', '.join(f"{_get(r, 'full_name') or _get(r, 'extension')} ({_get(r, 'extension')})" for r in found)
            item['reason'] = f'Подходит несколько сотрудников: {people}. {needs}'
        if not item['confirmed'] and pinned:
            item['reason'] += f' Доступ можно подтвердить списком {ENV}.'
        out.append(item)
    return out


def can_use(who) -> bool:
    """Фильтры — только действующему владельцу или руководителю из личной сессии."""
    actor = getattr(who, 'actor', None)
    return bool(who and getattr(who, 'role', None) == 'director' and actor is not None
                and getattr(actor, 'role', None) in ('owner', 'head')
                and not getattr(who, 'test', False) and not getattr(who, 'support', False))


def actor_row(rows, actor):
    """Запись сотрудника, под которой вошёл человек: по добавочному, затем по Telegram ID."""
    extension = norm(getattr(actor, 'extension', None))
    telegram_user_id = getattr(actor, 'telegram_user_id', None)
    if extension:
        for row in rows:
            if norm(_get(row, 'extension')) == extension:
                return row
    if telegram_user_id:
        for row in rows:
            if str(_get(row, 'telegram_user_id') or '') == str(telegram_user_id):
                return row
    return None


def matches(item: dict, row) -> bool:
    """Совпадает ли подтверждённое сопоставление с записью сотрудника."""
    if not item.get('confirmed'):
        return False
    if item.get('extension') and norm(item['extension']) == norm(_get(row, 'extension')):
        return True
    return bool(item.get('telegram_user_id')) and item['telegram_user_id'] == _get(row, 'telegram_user_id')


async def check(pool, who, env=None) -> dict:
    """Серверная проверка доступа к фильтрам. Отказ — это не ошибка, а состояние с причиной."""
    out = {'allowed': False, 'reason': '', 'targets': [], 'override': False, 'employee': None}
    if not can_use(who):
        out['reason'] = f'Фильтры «Колл-центра» доступны только владельцу и руководителям с личной привязкой: {WHO}.'
        return out
    client_id = getattr(who, 'company', None) or getattr(getattr(who, 'actor', None), 'client_id', None)
    rows = await employees(pool, client_id) if client_id else []
    targets = resolve(rows, env)
    pinned = env_tokens(env)
    out['targets'] = targets
    row = actor_row(rows, who.actor)
    if row is None:
        out['reason'] = f'Вход не привязан к записи сотрудника: сопоставить его с {WHO} не с чем.'
        return out
    out['employee'] = {'extension': _get(row, 'extension'), 'name': _get(row, 'full_name') or '',
                       'role': _get(row, 'role') or '',
                       'telegram_user_id': _get(row, 'telegram_user_id')}
    if any(token_matches(row, token) for token in pinned):
        out.update(allowed=True, override=True)
        return out
    confirmed = [item for item in targets if matches(item, row)]
    if confirmed:
        out['allowed'] = True
        return out
    reasons = '; '.join(item['reason'] for item in targets if not item['confirmed'])
    out['reason'] = (f'Доступ к фильтрам «Колл-центра» есть только у {WHO}. '
                     f'Ваша запись сотрудника не совпала ни с одним из них. {reasons}').strip()
    return out


async def allowed(pool, who, env=None) -> bool:
    """Короткий ответ для интерфейса и для проверки в API."""
    try:
        return bool((await check(pool, who, env))['allowed'])
    except Exception:  # noqa: BLE001 — нет таблицы/старой базы: доступ не выдаём
        return False
