"""Кому доступны фильтры периода и сотрудников в «Колл-центре»: Григорий, Евгений и pivondopola.

Доступ выдаётся только по проверяемому идентификатору учётной записи, а не по
совпадению имени: одинаковых «Григориев» в компании может быть сколько угодно, и
имя ничего не подтверждает. Проверяемые идентификаторы:

* ``username`` — точный ``employees.telegram_username``;
* ``tg`` — точный ``employees.telegram_user_id``;
* ``ext`` — точный добавочный ``employees.extension`` (в том числе ``head:…``).

Три идентификатора Telegram сверены с действующими учётными записями Averon.
Дополнительные идентификаторы задаются явно в настройке компании.

Проверка серверная: её вызывает API, а не интерфейс. Тестовый и служебный входы
фильтры не получают. При ошибке чтения сотрудников или настроек доступ закрыт.
"""
from __future__ import annotations

import re

from . import company

TARGETS = (
    {'key': 'grigory', 'label': 'Григорий', 'identifiers': (('tg', '841868875'),)},
    {'key': 'evgeny', 'label': 'Евгений', 'identifiers': (('tg', '304327068'),)},
    {'key': 'pivondopola', 'label': 'pivondopola', 'identifiers': (('tg', '562570177'),)},
)
ENV = 'CALLCENTER_FILTER_VIEWERS'
SETTING = 'callcenter_filter_viewers'
KINDS = {'tg': ('telegram_user_id', 'Telegram ID'), 'username': ('telegram_username', 'ник'),
         'ext': ('extension', 'добавочный')}
WHO = 'Григорий, Евгений и pivondopola'


def norm(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '').lower().replace('ё', 'е')).strip()


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, TypeError):
        return getattr(row, key, default)


def clean_value(kind: str, value) -> str:
    text = str(value or '').strip()
    if kind == 'username':
        return text.lstrip('@').lower()
    if kind == 'tg':
        return text if text.lstrip('-').isdigit() else ''
    return norm(text)


def parse_config(raw) -> tuple[list[dict], list[str]]:
    """Разобрать настройку: ``grigory=tg:901; evgeny=ext:head:evgeny; username:pivondopola``.

    Возвращает записи и понятные проблемы (без значений: настройка не пересказывается).
    """
    entries, problems = [], []
    keys = {target['key'] for target in TARGETS}
    for part in re.split(r'[;,\n]+', str(raw or '')):
        part = part.strip()
        if not part:
            continue
        target = None
        left, sep, right = part.partition('=')
        if sep and left.strip().lower() in keys:
            target, part = left.strip().lower(), right.strip()
        kind, sep, value = part.partition(':')
        kind = kind.strip().lower()
        if not sep and value == '' and part.startswith('@'):
            kind, value = 'username', part
            sep = ':'
        if kind not in KINDS or not sep:
            problems.append('Запись без вида идентификатора (username/tg/ext) пропущена.')
            continue
        value = clean_value(kind, value)
        if not value:
            problems.append(f'Пустое значение идентификатора вида {kind} пропущено.')
            continue
        entries.append({'target': target, 'kind': kind, 'value': value})
    return entries, problems


def _row_matches(row, kind: str, value: str) -> bool:
    return norm(_get(row, KINDS[kind][0])) == norm(value)


def _describe(row) -> str:
    return f"доб. {_get(row, 'extension') or '—'}"


def resolve(rows, entries) -> list[dict]:
    """Кто из трёх подтверждён данными, а кто нет — с причиной и видом идентификатора."""
    out = []
    used: dict[str, list[str]] = {}
    for target in TARGETS:
        builtin = [{'target': target['key'], 'kind': kind, 'value': clean_value(kind, value), 'builtin': True}
                   for kind, value in target.get('identifiers') or ()]
        custom = [{**entry, 'builtin': False} for entry in entries if entry['target'] in (None, target['key'])]
        ids = builtin + [item for item in custom if clean_value(item['kind'], item['value'])]
        item = {'key': target['key'], 'label': target['label'], 'confirmed': False, 'configured': False,
                'extension': None, 'telegram_user_id': None, 'name': '', 'role': '', 'via': '', 'reason': ''}
        if not ids:
            item['reason'] = (f'Для «{target["label"]}» нет подтверждённого идентификатора: в репозитории его нет, '
                              f'а доступ по совпадению имени не выдаётся. Укажите {ENV} или настройку компании '
                              f'«{SETTING}», например {target["key"]}=tg:<Telegram ID> или ext:<добавочный>.')
            out.append(item)
            continue
        found, via, configured = {}, '', False
        for entry in ids:
            for row in rows:
                if _row_matches(row, entry['kind'], entry['value']):
                    found[norm(_get(row, 'extension')) or _describe(row)] = row
                    via, configured = entry['kind'], not entry.get('builtin')
        if len(found) == 1:
            key, row = next(iter(found.items()))
            item.update(confirmed=True, configured=configured, via=via, extension=_get(row, 'extension'),
                        name=_get(row, 'full_name') or '', role=_get(row, 'role') or '',
                        telegram_user_id=_get(row, 'telegram_user_id'))
            used.setdefault(key, []).append(target['key'])
        elif not found:
            kinds = ', '.join(sorted({KINDS[entry['kind']][1] for entry in ids}))
            item['reason'] = (f'Среди активных владельцев и руководителей нет записи с подтверждённым '
                              f'идентификатором «{target["label"]}» ({kinds}). Доступ по имени не выдаётся: '
                              f'проверьте {ENV} / «{SETTING}» и запись в «Сотрудниках».')
        else:
            item['reason'] = (f'Под идентификатор «{target["label"]}» подходят несколько записей: '
                              f'{", ".join(sorted(found))}. Доступ не выдаётся, пока не останется одна.')
        out.append(item)
    for item in out:  # одна и та же запись не может подтверждать двух разных людей
        if item['confirmed'] and len(used.get(norm(item['extension']) or '', [])) > 1:
            item.update(confirmed=False, reason='Одна запись сотрудника подходит нескольким адресатам доступа: '
                                               'уточните идентификаторы в настройке.')
    return out


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


async def config_of(pool, client_id) -> str:
    """Настройка компании; у нашей компании без строки в базе — та же переменная окружения."""
    try:
        return str(await company.setting(pool, client_id, SETTING) or '')
    except Exception:  # noqa: BLE001 — настройки не прочитаны: доступа нет, причина ниже
        return ''


async def check(pool, who, config=None) -> dict:
    """Серверная проверка доступа к фильтрам. Отказ — это состояние с причиной, не ошибка."""
    out = {'allowed': False, 'reason': '', 'targets': [], 'override': False, 'employee': None, 'problems': []}
    if not can_use(who):
        out['reason'] = f'Фильтры «Колл-центра» доступны только владельцу и руководителям с личной привязкой: {WHO}.'
        return out
    client_id = getattr(who, 'company', None) or getattr(getattr(who, 'actor', None), 'client_id', None)
    if config is None:
        config = await config_of(pool, client_id)
    entries, problems = parse_config(config)
    out['problems'] = problems
    try:
        rows = await employees(pool, client_id) if client_id else []
    except Exception:  # noqa: BLE001 — нет таблицы сотрудников: доступ закрыт, а не открыт
        out['reason'] = 'Список сотрудников компании не прочитан: доступ к фильтрам не выдан.'
        return out
    targets = resolve(rows, entries)
    out['targets'] = targets
    row = actor_row(rows, who.actor)
    if row is None:
        out['reason'] = f'Вход не привязан к записи сотрудника: сопоставить его с {WHO} не с чем.'
        return out
    out['employee'] = {'extension': _get(row, 'extension'), 'name': _get(row, 'full_name') or '',
                       'role': _get(row, 'role') or '', 'telegram_user_id': _get(row, 'telegram_user_id')}
    confirmed = [item for item in targets if matches(item, row)]
    if confirmed:
        out['allowed'] = True
        out['override'] = any(item.get('configured') for item in confirmed)
        return out
    confirmed_names = ', '.join(f"{item['label']} ({KINDS[item['via']][1]})" for item in targets
                                if item['confirmed'] and item['via'] in KINDS) or 'никто не подтверждён'
    reasons = ' '.join(item['reason'] for item in targets if not item['confirmed'])
    out['reason'] = (f'Доступ к фильтрам «Колл-центра» есть только у {WHO}. Ваша запись сотрудника '
                     f'({_describe(row)}) не совпала с подтверждёнными: {confirmed_names}. {reasons}').strip()
    return out


async def allowed(pool, who, config=None) -> bool:
    """Короткий ответ для интерфейса и для проверки в API. Ошибка — отказ."""
    try:
        return bool((await check(pool, who, config))['allowed'])
    except Exception:  # noqa: BLE001 — нет таблицы/старой базы: доступ не выдаём
        return False
