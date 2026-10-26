"""Кабинет голосового робота: операторы, контакты, стоп-лист, кампании, звонки, аналитика.

Как у Соловья, но на нашем роботе. Кабинеты разделены по owner: '' — наш
(директор), иначе лист клиента в «Прозвон лидов». Клиенту кабинет открывается
платной опцией: директор включает его в «Настройках» и задаёт цену минуты,
клиент видит стоимость своих разговоров по этой цене (посекундно).

Операторы лежат TOML-файлами рядом с данными робота (voice.operator.EXTRA_DIR):
CRM пишет, робот читает перед каждым звонком. Итоги звонков робот дописывает в
calls.jsonl; robot_runner переносит их в robot_calls и ведёт кампании.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import re
import secrets
import tomllib
import zipfile
from datetime import datetime, time, timedelta, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools import Forbidden
from .dial import normalize
from . import robot
from .robot import OUTCOMES
from .robot_prices import num, status as auto_prices

PAID_OPTION = 'Голосовой робот подключается отдельно, за доплату. Чтобы включить, напишите вашему менеджеру Averon.'
READONLY = 'Тестовый вход только для просмотра.'
COST_KEYS = ('mango_min', 'eleven_1k_chars', 'llm_1k_tokens')
MAX_CLIENT_MIN = 1000.0  # цена минуты для клиента выше этого — опечатка
ASR_15S = 0.1626  # ₽ с НДС за 15 с потокового распознавания SpeechKit (прайс AI Studio на 21.10.2026)
CALC_DAYS = 30  # сколько дней звонков робота брать для расхода символов и токенов на минуту
# 22.10 простые ответы по анкете пошли по заготовкам без модели: звонки до этого тратили токенов втрое больше.
# Расход считаем по звонкам после изменения; пока их меньше FRESH_MINUTES минут — по старым звонкам,
# а токены умножаем на долю из автотеста (обычный звонок: 4 319 токенов вместо 12 840).
QUICK_SINCE = datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc)
QUICK_TOKEN_RATIO = round(4319 / 12840, 2)
FRESH_MINUTES = 10
STATUSES = {'draft': 'Черновик', 'running': 'Идёт', 'paused': 'На паузе', 'done': 'Завершена'}
TIMEZONES = [('Europe/Kaliningrad', 'Калининград, МСК−1'), ('Europe/Moscow', 'Москва, МСК'), ('Europe/Samara', 'Самара, МСК+1'),
             ('Asia/Yekaterinburg', 'Екатеринбург, МСК+2'), ('Asia/Omsk', 'Омск, МСК+3'), ('Asia/Novosibirsk', 'Новосибирск, МСК+4'),
             ('Asia/Krasnoyarsk', 'Красноярск, МСК+4'), ('Asia/Irkutsk', 'Иркутск, МСК+5'), ('Asia/Yakutsk', 'Якутск, МСК+6'),
             ('Asia/Vladivostok', 'Владивосток, МСК+7'), ('Asia/Magadan', 'Магадан, МСК+8'), ('Asia/Kamchatka', 'Камчатка, МСК+9')]
MAX_CONTACTS = 20000
RECORDING_DAYS = 10
# Поля анкеты, которые правятся в CRM. Остальное (голос, быстрые ответы) берётся из оператора, с которого сделана копия.
TEXT_FIELDS = ('name', 'gender', 'company', 'lead_source', 'goal', 'greeting', 'farewell', 'transfer_to', 'example')
LIST_FIELDS = ('plan', 'rules', 'facts')


# ------------------------------------------------------------------ доступ и настройки


async def get_option(pool, key: str, default):
    value = await pool.fetchval('SELECT value FROM robot_options WHERE key=$1', key)
    return default if value is None else json.loads(value)


async def set_option(pool, key: str, value) -> None:
    await pool.execute('INSERT INTO robot_options(key,value) VALUES($1,$2::jsonb) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value',
                       key, json.dumps(value, ensure_ascii=False))


async def prices(pool) -> dict:
    """Себестоимость считает robot_prices сам; директор задаёт только цену минуты для каждого клиента."""
    auto = await auto_prices(pool)
    out = {key: float((auto.get(key) or {}).get('value') or 0) for key in COST_KEYS}
    from voice import presets, speech
    out['tts_1k_chars'] = presets.tts_rub_1k(presets.tts_model() or speech.eleven_params()[0], out['eleven_1k_chars'])
    out['client_min'] = {k: float(v) for k, v in ((await get_option(pool, 'prices', {})).get('client_min') or {}).items()}
    return out


def client_charge(seconds: float, p: dict, owner: str) -> float | None:
    """Сколько клиент платит за разговоры: минуты (посекундно) × его цена минуты; None, если цена не задана."""
    price = p['client_min'].get(owner)
    return None if price is None else round(seconds / 60 * price, 2)


async def enabled_clients(pool) -> list[str]:
    return list(await get_option(pool, 'clients', []))


async def owner_for(pool, who, requested=None, write: bool = False) -> str:
    """Чей кабинет открыт. Клиенту — только свой и только если опция включена; директору — любой."""
    if write and who.test:
        raise Forbidden(READONLY)
    if who.role == 'client':
        if not who.client_key or who.client_key not in await enabled_clients(pool):
            raise Forbidden(PAID_OPTION)
        return who.client_key
    if who.role != 'director':
        raise Forbidden('Раздел директора.')
    owner = str(requested or '').strip()
    if owner and owner not in await enabled_clients(pool):
        raise Forbidden('Кабинет робота этому клиенту не включён.')
    return owner


async def state(pool, who) -> dict:
    """С чего начинается страница: чей кабинет, что можно, справочники."""
    director = who.role == 'director'
    clients = await enabled_clients(pool)
    enabled = director or (who.client_key in clients)
    return {'role': who.role, 'enabled': enabled, 'paid_note': None if enabled else PAID_OPTION,
            'owners': ([{'id': '', 'name': 'Averon (наш)'}] + [{'id': c, 'name': c} for c in clients]) if director else [],
            'readonly': who.test, 'outcomes': OUTCOMES, 'statuses': STATUSES, 'timezones': TIMEZONES,
            'recording_days': RECORDING_DAYS}


async def save_options(pool, who, data: dict) -> dict:
    if who.role != 'director':
        raise Forbidden('Раздел директора.')
    if who.test:
        raise Forbidden(READONLY)
    if 'prices' in data:  # руками задаётся только цена минуты для клиентов, себестоимость считается сама
        got = data['prices'] if isinstance(data['prices'], dict) else {}
        clean = {}
        for name, raw in (got.get('client_min') or {}).items():
            name = str(name).strip()[:100]
            if not name or str(raw).strip() == '':
                continue  # пустое поле — цена не задана
            value = num(raw)
            if value is None or not 0 <= value <= MAX_CLIENT_MIN:
                raise ValueError(f'Цена минуты для «{name}» должна быть числом от 0 до {MAX_CLIENT_MIN:g} ₽.')
            clean[name] = value
        await set_option(pool, 'prices', {'client_min': clean})
    if 'clients' in data:
        names = data['clients'] if isinstance(data['clients'], list) else []
        await set_option(pool, 'clients', sorted({str(n).strip()[:100] for n in names if str(n).strip()}))
    return {'prices': await prices(pool), 'clients': await enabled_clients(pool), 'econ': await economics(pool)}


# ------------------------------------------------------------------ операторы


def _read(path) -> dict:
    return tomllib.loads(path.read_text(encoding='utf-8'))


def operators(owner: str) -> list[dict]:
    from voice import operator as vop
    out = []
    for op_id in vop.all_ids():
        try:
            op = vop.load(op_id)
        except vop.OperatorError:
            continue
        if op.owner != owner and not (owner == '' and vop.shipped(op_id)):
            continue
        out.append({'id': op.id, 'name': op.name, 'company': op.company, 'goal': op.goal, 'builtin': vop.shipped(op_id),
                    'edited': vop.shipped(op_id) and not vop.builtin(op_id),
                    'plan': len(op.plan), 'rules': len(op.rules), 'facts': len(op.facts)})
    return out


def _owned(op_id: str, owner: str, write: bool = False):
    from voice import operator as vop
    path = vop.find(op_id)
    if path is None:
        raise ValueError('Нет такого оператора.')
    data = _read(path)
    builtin = vop.shipped(op_id)
    if (data.get('owner') or '') != owner and not (owner == '' and builtin):
        raise Forbidden('Это оператор другого кабинета.')
    if write and builtin:
        # встроенного правит только директор (кабинеты клиентов их не видят): правка ложится рядом под тем же id
        path = vop.EXTRA_DIR / f'{op_id}.toml'
    return path, data, builtin


def operator(owner: str, op_id: str) -> dict:
    from voice import operator as vop
    _, data, builtin = _owned(op_id, owner)
    fields = data.get('fields') or {}
    return {'id': op_id, 'builtin': builtin, 'edited': builtin and not vop.builtin(op_id), **{k: data.get(k, '') for k in TEXT_FIELDS},
            **{k: list(data.get(k) or []) for k in LIST_FIELDS},
            'fields': [{'key': k, 'text': v} for k, v in fields.items()], 'max_items': 10}


def save_operator(owner: str, data: dict, op_id: str | None = None, copy_from: str | None = None) -> dict:
    """Новый оператор (op_id=None, можно copy_from) или правка своего. Пишет TOML для робота."""
    from voice import operator as vop
    if op_id:
        path, base, _ = _owned(op_id, owner, write=True)
    else:
        base = _owned(copy_from, owner)[1] if copy_from else {}
        op_id = 'op-' + secrets.token_hex(4)
        path = vop.EXTRA_DIR / f'{op_id}.toml'
    new = {k: v for k, v in base.items() if k not in ('owner', 'fields')}
    for key in TEXT_FIELDS:
        if key in data:
            new[key] = str(data.get(key) or '').strip()[:4000 if key == 'example' else 1000]
    new['gender'] = new.get('gender') or 'female'
    for key in LIST_FIELDS:
        if key in data:
            items = data.get(key) if isinstance(data.get(key), list) else []
            new[key] = [str(x).strip()[:1000] for x in items if str(x).strip()]
    if not new.get('lead_source'):
        new['lead_source'] = 'оставлял заявку'
    fields = {}
    for i, item in enumerate(data.get('fields') if isinstance(data.get('fields'), list) else (base.get('fields') or {}).items()):
        key, text = (item.get('key'), item.get('text')) if isinstance(item, dict) else item
        key = str(key or '').strip()
        text = str(text or '').strip()[:300]
        if not text:
            continue
        if not key.isidentifier() or key in fields:
            key = f'f{i + 1}'
        fields[key] = text
    # быстрые ответы копия встроенного оператора берёт у него при каждом звонке (voice.operator._builtin_quick),
    # своих не храним: так до неё доходят новые заготовки
    if copy_from and vop.shipped(copy_from):
        new['copy_of'] = copy_from
    if new.get('copy_of') or vop.shipped(op_id):
        new.pop('quick', None)
    elif base.get('quick') and (new.get('greeting') != base.get('greeting') or new.get('plan') != base.get('plan')):
        new.pop('quick', None)
    if owner:
        new['owner'] = owner
    new['fields'] = fields
    try:
        vop.from_dict(op_id, new)
    except vop.OperatorError as exc:
        raise ValueError(_human_error(str(exc))) from None
    if not robot.DATA.is_dir():
        raise ValueError('Робот не установлен на этом сервере: сохранить оператора некуда.')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(vop.dump(new), encoding='utf-8')
    tmp.replace(path)
    return operator(owner, op_id)


NAMES = {'name': 'имя', 'company': 'компания', 'greeting': 'приветствие', 'farewell': 'прощание', 'goal': 'цель', 'example': 'образец разговора',
         'plan': 'план разговора', 'rules': 'правила', 'facts': 'аргументы'}


def _human_error(text: str) -> str:
    text = text.split(': ', 1)[-1]
    for key, name in NAMES.items():
        text = re.sub(rf'\b{key}\b', name, text)
    return text[:1].upper() + text[1:]


async def delete_operator(pool, owner: str, op_id: str) -> None:
    """Свой оператор удаляем, у встроенного удаляем правку из CRM: он снова как в репозитории."""
    from voice import operator as vop
    path, _, builtin = _owned(op_id, owner, write=True)
    if builtin:
        if not path.is_file():
            raise ValueError('Встроенный оператор не правили: возвращать нечего.')
        path.unlink()
        return
    if await pool.fetchval("SELECT 1 FROM robot_campaigns WHERE operator=$1 AND status IN ('running','paused')", op_id):
        raise ValueError('Оператор работает в кампании. Сначала завершите её.')
    path.unlink()


# ------------------------------------------------------------------ контакты


PHONE_HEAD = ('тел', 'phone', 'номер')
NAME_HEAD = ('имя', 'фио', 'name', 'клиент')
NOTE_HEAD = ('комм', 'замет', 'note', 'comment', 'примеч')


def xlsx_rows(data: bytes) -> list[list[str]]:
    """Первый лист .xlsx без сторонних библиотек: общие строки и значения ячеек по порядку столбцов."""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        shared = []
        if 'xl/sharedStrings.xml' in book.namelist():
            for si in ElementTree.fromstring(book.read('xl/sharedStrings.xml')).findall('m:si', ns):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])))
        sheets = sorted(n for n in book.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n))
        root = ElementTree.fromstring(book.read(sheets[0]))
    except (zipfile.BadZipFile, KeyError, IndexError, ElementTree.ParseError):
        raise ValueError('Не получилось прочитать файл Excel. Сохраните его как .xlsx или .csv.')
    rows = []
    for row in root.iter('{%s}row' % ns['m']):
        cells = {}
        for c in row.findall('m:c', ns):
            ref = re.match(r'[A-Z]+', c.get('r', 'A'))
            col = 0
            for ch in ref.group(0) if ref else 'A':
                col = col * 26 + ord(ch) - 64
            v = c.find('m:v', ns)
            if c.get('t') == 's' and v is not None:
                value = shared[int(v.text)] if v.text and v.text.isdigit() and int(v.text) < len(shared) else ''
            elif c.get('t') == 'inlineStr':
                value = ''.join(t.text or '' for t in c.iter('{%s}t' % ns['m']))
            else:
                value = v.text if v is not None and v.text else ''
                if re.fullmatch(r'\d+\.0', value):
                    value = value[:-2]
                elif re.fullmatch(r'\d(\.\d+)?E\+\d+', value):  # номер, сохранённый числом
                    value = str(int(float(value)))
            cells[col - 1] = value
        if cells:
            rows.append([cells.get(i, '') for i in range(max(cells) + 1)])
    return rows


def csv_rows(text: str) -> list[list[str]]:
    sample = text[:4000]
    delimiter = max(';,\t', key=sample.count) if any(d in sample for d in ';,\t') else ','
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def contacts_from_rows(rows: list[list[str]]) -> tuple[list[dict], int]:
    """Номера, имена и заметки из таблицы. Столбцы ищем по заголовку, иначе первый столбец с номерами."""
    rows = [[str(c or '').strip() for c in r] for r in rows if any(str(c or '').strip() for c in r)]
    if not rows:
        return [], 0
    head = [c.lower() for c in rows[0]]
    find = lambda keys: next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)  # noqa: E731
    p, n, t = find(PHONE_HEAD), find(NAME_HEAD), find(NOTE_HEAD)
    body = rows[1:] if p is not None or n is not None else rows
    if p is None:
        counts = {}
        for r in body[:200]:
            for i, c in enumerate(r):
                if normalize(c):
                    counts[i] = counts.get(i, 0) + 1
        if not counts:
            raise ValueError('В файле не нашлось телефонных номеров.')
        p = max(counts, key=counts.get)
    out, bad, seen = [], 0, set()
    for r in body:
        number = normalize(r[p] if p < len(r) else '')
        if not number:
            bad += 1
            continue
        if number in seen:
            continue
        seen.add(number)
        out.append({'phone': number, 'name': (r[n] if n is not None and n < len(r) else '')[:200],
                    'note': (r[t] if t is not None and t < len(r) else '')[:500]})
    return out, bad


async def import_contacts(pool, owner: str, contacts: list[dict], source: str) -> dict:
    have = await pool.fetchval('SELECT count(*) FROM robot_contacts WHERE owner=$1', owner)
    if have + len(contacts) > MAX_CONTACTS:
        raise ValueError(f'В кабинете можно держать до {MAX_CONTACTS} контактов.')
    async with pool.acquire() as con:
        for c in contacts:
            await con.execute(
                """INSERT INTO robot_contacts(owner,phone,name,note,source) VALUES($1,$2,$3,$4,$5)
                   ON CONFLICT (owner,phone) DO UPDATE SET name=CASE WHEN EXCLUDED.name<>'' THEN EXCLUDED.name ELSE robot_contacts.name END,
                     note=CASE WHEN EXCLUDED.note<>'' THEN EXCLUDED.note ELSE robot_contacts.note END""",
                owner, c['phone'], c.get('name') or '', c.get('note') or '', source[:100])
    total = await pool.fetchval('SELECT count(*) FROM robot_contacts WHERE owner=$1', owner)
    return {'added': total - have, 'total': total}  # только новые: повторы обновляют имя и заметку, но не считаются


async def upload_contacts(pool, owner: str, data: dict) -> dict:
    name = str(data.get('name') or 'файл')[:100]
    raw = str(data.get('data') or '')
    if name.lower().endswith('.xlsx'):
        try:
            rows = xlsx_rows(base64.b64decode(raw))
        except (ValueError, TypeError) as exc:
            raise ValueError(str(exc) if 'Excel' in str(exc) else 'Файл не прочитался.') from None
    elif name.lower().endswith('.xls'):
        raise ValueError('Старый формат .xls не читается. Сохраните файл как .xlsx или .csv.')
    else:
        text = str(data.get('text') or '')
        if not text and raw:
            blob = base64.b64decode(raw)
            for enc in ('utf-8-sig', 'cp1251'):
                try:
                    text = blob.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        rows = csv_rows(text)
    contacts, bad = contacts_from_rows(rows)
    if not contacts:
        raise ValueError('В файле не нашлось телефонных номеров.')
    result = await import_contacts(pool, owner, contacts, name)
    return {**result, 'skipped': bad}


async def sheet_tabs(who) -> list[str]:
    """Листы «Прозвон лидов», откуда можно взять номера. Клиенту только его лист."""
    from . import gsheets
    sheet = gsheets.leads_sheet()
    if not sheet:
        return []
    if who.role == 'client':
        return [who.client_key]
    r = await gsheets._client.get(f'https://sheets.googleapis.com/v4/spreadsheets/{sheet}',
                                  params={'fields': 'sheets.properties.title'},
                                  headers={'Authorization': 'Bearer ' + await gsheets._bearer()})
    r.raise_for_status()
    return [s['properties']['title'] for s in r.json().get('sheets', [])]


async def sheet_contacts(pool, who, owner: str, tab: str) -> dict:
    from . import gsheets
    sheet = gsheets.leads_sheet()
    if not sheet:
        raise ValueError('Таблица «Прозвон лидов» не подключена.')
    tab = str(tab or '').strip()
    if tab not in await sheet_tabs(who):
        raise ValueError('Такого листа нет.')
    rows = await gsheets.values(sheet, "'" + tab.replace("'", "''") + "'!A1:Z20000")
    contacts, bad = contacts_from_rows(rows)
    if not contacts:
        raise ValueError('На листе не нашлось телефонных номеров.')
    return {**await import_contacts(pool, owner, contacts, 'лист ' + tab), 'skipped': bad}


async def contacts(pool, owner: str, q: str = '', limit: int = 500) -> dict:
    q = str(q or '').strip()
    digits = ''.join(ch for ch in q if ch.isdigit())
    rows = await pool.fetch(
        """SELECT c.id, c.phone, c.name, c.note, c.source, c.created_at, s.phone IS NOT NULL AS stopped
           FROM robot_contacts c LEFT JOIN robot_stoplist s ON s.owner=c.owner AND s.phone=c.phone
           WHERE c.owner=$1 AND ($2='' OR c.name ILIKE '%'||$2||'%' OR c.note ILIKE '%'||$2||'%' OR ($3<>'' AND c.phone LIKE '%'||$3||'%'))
           ORDER BY c.id DESC LIMIT $4""", owner, q, digits, limit)
    total = await pool.fetchval('SELECT count(*) FROM robot_contacts WHERE owner=$1', owner)
    return {'total': total, 'items': [{'id': r['id'], 'phone': r['phone'], 'name': r['name'], 'note': r['note'],
                                       'source': r['source'], 'stopped': r['stopped']} for r in rows]}


async def delete_contacts(pool, owner: str, ids) -> int:
    ids = [int(i) for i in (ids or []) if str(i).isdigit() and len(str(i)) <= 18]
    status = await pool.execute('DELETE FROM robot_contacts WHERE owner=$1 AND id=ANY($2)', owner, ids)
    return int(status.split()[-1])


# ------------------------------------------------------------------ стоп-лист


async def stoplist(pool, owner: str) -> list[dict]:
    rows = await pool.fetch('SELECT phone, reason, created_at FROM robot_stoplist WHERE owner=$1 ORDER BY created_at DESC', owner)
    return [{'phone': r['phone'], 'reason': r['reason'], 'at': r['created_at'].isoformat(timespec='minutes')} for r in rows]


async def stop_add(pool, owner: str, text: str, reason: str = '') -> int:
    numbers = []
    for part in re.split(r'[\n,;]+', str(text or '')):
        number = normalize(part)
        if number:
            numbers.append(number)
    if not numbers:
        raise ValueError('Не нашёл номеров. По одному в строке.')
    for number in numbers:
        await pool.execute('INSERT INTO robot_stoplist(owner,phone,reason) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
                           owner, number, str(reason or 'вручную')[:200])
    return len(numbers)


async def stop_remove(pool, owner: str, number) -> None:
    await pool.execute('DELETE FROM robot_stoplist WHERE owner=$1 AND phone=$2', owner, normalize(number) or str(number or ''))


# ------------------------------------------------------------------ кампании


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Неизвестный часовой пояс.')


def _clock(value, default: str) -> time:
    try:
        return time.fromisoformat(str(value or default)[:5])
    except ValueError:
        raise ValueError('Время в виде 10:00.')


def clean_campaign(data: dict, owner: str) -> dict:
    name = str(data.get('name') or '').strip()[:120]
    if not name:
        raise ValueError('Назовите кампанию.')
    op_id = str(data.get('operator') or '')
    if op_id not in {o['id'] for o in operators(owner)}:
        raise ValueError('Выберите оператора.')
    days = sorted({int(d) for d in (data.get('days') or []) if str(d).isdigit() and 1 <= int(d) <= 7})
    if not days:
        raise ValueError('Выберите хотя бы один день недели.')
    start, end = _clock(data.get('start_time'), '10:00'), _clock(data.get('end_time'), '19:00')
    if start >= end:
        raise ValueError('Время начала должно быть раньше окончания.')
    tz = str(data.get('timezone') or 'Europe/Moscow')
    _zone(tz)
    try:
        attempts = int(data.get('attempts') or 1)
        retry = int(data.get('retry_minutes') or 120)
    except (TypeError, ValueError):
        raise ValueError('Попытки и перерыв — числа.')
    if not 1 <= attempts <= 10:
        raise ValueError('Попыток дозвона от 1 до 10.')
    if not 10 <= retry <= 7 * 24 * 60:
        raise ValueError('Перерыв между попытками от 10 минут до недели.')
    return {'name': name, 'operator': op_id, 'days': days, 'start_time': start, 'end_time': end, 'timezone': tz,
            'attempts': attempts, 'retry_minutes': retry}


def campaign_item(r) -> dict:
    return {'id': r['id'], 'name': r['name'], 'operator': r['operator'], 'days': list(r['days']),
            'start_time': r['start_time'].strftime('%H:%M'), 'end_time': r['end_time'].strftime('%H:%M'),
            'timezone': r['timezone'], 'attempts': r['attempts'], 'retry_minutes': r['retry_minutes'],
            'status': r['status'], 'status_name': STATUSES.get(r['status'], r['status']),
            'total': r['total'], 'done': r['done_count'], 'called': r['called'], 'interested': r['interested'],
            'created': r['created_at'].isoformat(timespec='minutes')}


CAMPAIGN_SQL = """SELECT c.*, count(t.id) AS total, count(t.id) FILTER (WHERE t.done) AS done_count,
    count(t.id) FILTER (WHERE t.attempts>0) AS called, count(t.id) FILTER (WHERE t.outcome='interested') AS interested
    FROM robot_campaigns c LEFT JOIN robot_targets t ON t.campaign_id=c.id"""


async def campaigns(pool, owner: str) -> list[dict]:
    rows = await pool.fetch(CAMPAIGN_SQL + ' WHERE c.owner=$1 GROUP BY c.id ORDER BY c.id DESC', owner)
    return [campaign_item(r) for r in rows]


async def _campaign(pool, owner: str, campaign_id: int):
    row = await pool.fetchrow(CAMPAIGN_SQL + ' WHERE c.owner=$1 AND c.id=$2 GROUP BY c.id', owner, campaign_id)
    if row is None:
        raise ValueError('Нет такой кампании.')
    return row


async def save_campaign(pool, owner: str, data: dict, campaign_id: int | None = None) -> dict:
    c = clean_campaign(data, owner)
    async with pool.acquire() as con, con.transaction():
        if campaign_id is None:
            campaign_id = await con.fetchval(
                """INSERT INTO robot_campaigns(owner,name,operator,days,start_time,end_time,timezone,attempts,retry_minutes)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
                owner, c['name'], c['operator'], c['days'], c['start_time'], c['end_time'], c['timezone'], c['attempts'],
                c['retry_minutes'])
        else:
            await _campaign(con, owner, campaign_id)
            await con.execute(
                """UPDATE robot_campaigns SET name=$3,operator=$4,days=$5,start_time=$6,end_time=$7,timezone=$8,attempts=$9,
                   retry_minutes=$10 WHERE owner=$1 AND id=$2""",
                owner, campaign_id, c['name'], c['operator'], c['days'], c['start_time'], c['end_time'], c['timezone'],
                c['attempts'], c['retry_minutes'])
        pick = data.get('contacts')
        if pick == 'all' or isinstance(pick, list):
            ids = None if pick == 'all' else [int(i) for i in pick if str(i).isdigit() and len(str(i)) <= 18]
            await con.execute(
                """INSERT INTO robot_targets(campaign_id,phone,name)
                   SELECT $1, c.phone, c.name FROM robot_contacts c WHERE c.owner=$2 AND ($3::bigint[] IS NULL OR c.id=ANY($3))
                   ON CONFLICT (campaign_id,phone) DO NOTHING""", campaign_id, owner, ids)
    return campaign_item(await _campaign(pool, owner, campaign_id))


async def campaign_action(pool, owner: str, campaign_id: int, action: str) -> dict:
    row = await _campaign(pool, owner, campaign_id)
    if action == 'start':
        if not row['total']:
            raise ValueError('В кампании нет номеров. Добавьте контакты.')
        if row['operator'] not in {o['id'] for o in operators(owner)}:
            raise ValueError('Оператора кампании больше нет. Выберите другого.')
        if row['done_count'] >= row['total']:
            raise ValueError('Все номера кампании уже обзвонены.')
        status = 'running'
    elif action == 'pause':
        status = 'paused'
    elif action == 'finish':
        status = 'done'
    elif action == 'delete':
        await pool.execute('DELETE FROM robot_campaigns WHERE owner=$1 AND id=$2', owner, campaign_id)
        return {'deleted': True}
    else:
        raise ValueError('Неизвестное действие.')
    await pool.execute('UPDATE robot_campaigns SET status=$3 WHERE owner=$1 AND id=$2', owner, campaign_id, status)
    return campaign_item(await _campaign(pool, owner, campaign_id))


async def targets(pool, owner: str, campaign_id: int) -> list[dict]:
    await _campaign(pool, owner, campaign_id)
    rows = await pool.fetch(
        """SELECT t.*, s.phone IS NOT NULL AS stopped FROM robot_targets t
           LEFT JOIN robot_stoplist s ON s.owner=$2 AND s.phone=t.phone WHERE t.campaign_id=$1 ORDER BY t.id LIMIT 2000""",
        campaign_id, owner)
    # номер внесли в стоп-лист после запуска: робот его пропустит на следующем шаге, показываем это сразу
    return [{'phone': r['phone'], 'name': r['name'], 'attempts': r['attempts'], 'done': r['done'] or r['stopped'],
             'outcome': {**OUTCOMES, 'stop': 'В стоп-листе', 'error': 'Робот не смог позвонить'}.get(
                 r['outcome'] or ('stop' if r['stopped'] and not r['done'] else ''), r['outcome'] or ''),
             'next_at': r['next_at'].isoformat(timespec='minutes') if r['next_at'] and not (r['done'] or r['stopped']) else None} for r in rows]


def in_window(c, now: datetime) -> bool:
    local = now.astimezone(_zone(c['timezone']))
    return local.isoweekday() in c['days'] and c['start_time'] <= local.time().replace(tzinfo=None) < c['end_time']


# ------------------------------------------------------------------ звонки и аналитика


def asr_cost(seconds: float) -> float:
    """Распознавание Яндекса слушает весь разговор, счёт идёт кусками по 15 секунд с округлением вверх."""
    return math.ceil(seconds / 15) * ASR_15S if seconds > 0 else 0.0


def call_cost(r, p: dict) -> float:
    return (r['duration_s'] / 60 * p['mango_min'] + r['tts_chars'] / 1000 * p['tts_1k_chars']
            + r['llm_tokens'] / 1000 * p['llm_1k_tokens'] + asr_cost(r['duration_s']))


def _filters(owner: str, f: dict) -> tuple[str, list]:
    where, args = ['owner=$1'], [owner]
    if str(f.get('campaign') or '').isdigit():
        args.append(int(f['campaign']))
        where.append(f'campaign_id=${len(args)}')
    if f.get('outcome') in OUTCOMES:
        args.append(f['outcome'])
        where.append(f'outcome=${len(args)}')
    for key, op in (('from', '>='), ('to', '<')):
        if f.get(key):
            try:
                day = datetime.fromisoformat(str(f[key])[:10]).replace(tzinfo=ZoneInfo('Europe/Moscow'))
            except ValueError:
                raise ValueError('Неверная дата.')
            args.append(day + timedelta(days=1) if key == 'to' else day)
            where.append(f'at {op} ${len(args)}')
    return ' AND '.join(where), args


async def calls(pool, who, owner: str, f: dict, limit: int = 500) -> dict:
    where, args = _filters(owner, f)
    rows = await pool.fetch(f'SELECT * FROM robot_calls WHERE {where} ORDER BY at DESC LIMIT {int(limit)}', *args)
    p = await prices(pool)
    client = who.role == 'client'
    names = {r['id']: r['name'] for r in await pool.fetch('SELECT id, name FROM robot_campaigns WHERE owner=$1', owner)}
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECORDING_DAYS)
    data = {r['call_id']: json.loads(r['data'] or '{}') for r in rows}
    return {'items': [{
        'id': r['call_id'], 'at': r['at'].astimezone(ZoneInfo('Europe/Moscow')).isoformat(timespec='minutes'), 'phone': r['phone'],
        'campaign': names.get(r['campaign_id'], ''), 'campaign_id': r['campaign_id'], 'outcome_key': r['outcome'],
        'outcome': OUTCOMES.get(r['outcome'], r['outcome']), 'good': r['outcome'] in ('interested', 'callback'),
        'comment': r['comment'], 'seconds': r['duration_s'], 'cost': client_charge(r['duration_s'], p, owner) if client else round(call_cost(r, p), 2),
        'recording': bool(r['recording']) and r['at'] > cutoff and (robot.DATA / 'recordings' / r['recording']).is_file(),
        'facts': data[r['call_id']].get('facts') or {},
        'transcript': [{'who': 'Робот' if w == 'robot' else 'Клиент' if w == 'client' else w, 'text': t}
                       for w, t in (data[r['call_id']].get('transcript') or [])],
    } for r in rows]}


def calls_csv(items: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['Дата', 'Телефон', 'Кампания', 'Статус', 'Длительность, с', 'Комментарий', 'Стоимость, ₽'])
    for c in items:
        w.writerow([c['at'].replace('T', ' '), c['phone'], c['campaign'], c['outcome'], c['seconds'], c['comment'], c['cost']])
    return '﻿' + out.getvalue()  # BOM: Excel открывает кириллицу без кракозябр


async def recording_path(pool, owner: str, call_id: str):
    name = await pool.fetchval('SELECT recording FROM robot_calls WHERE owner=$1 AND call_id=$2', owner, str(call_id))
    path = robot.DATA / 'recordings' / (name or '')
    if not name or '/' in name or not path.is_file():
        raise LookupError(f'Записи нет: они хранятся {RECORDING_DAYS} дней.')
    return path


async def analytics(pool, who, owner: str, f: dict) -> dict:
    where, args = _filters(owner, f)
    rows = await pool.fetch(f'SELECT at, outcome, duration_s, tts_chars, llm_tokens FROM robot_calls WHERE {where}', *args)
    p = await prices(pool)
    total = len(rows)
    reached = sum(r['outcome'] != 'ndz' for r in rows)
    leads = sum(r['outcome'] == 'interested' for r in rows)
    cost = sum(call_cost(r, p) for r in rows)
    charge = client_charge(sum(r['duration_s'] for r in rows), p, owner)
    if who.role == 'client':
        cost = charge
    days: dict[str, dict] = {}
    for r in rows:
        day = r['at'].astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
        d = days.setdefault(day, {'day': day, 'calls': 0, 'reached': 0, 'leads': 0})
        d['calls'] += 1
        d['reached'] += r['outcome'] != 'ndz'
        d['leads'] += r['outcome'] == 'interested'
    ends = {key: sum(r['outcome'] == key for r in rows) for key in OUTCOMES}
    priced = cost is not None and (who.role == 'client' or cost > 0 or any(p[key] for key in COST_KEYS))
    out = {'total': total, 'reached': reached, 'leads': leads,
           'dial_rate': round(reached / total * 100, 1) if total else 0, 'conversion': round(leads / total * 100, 1) if total else 0,
           'minutes': round(sum(r['duration_s'] for r in rows) / 60, 1), 'priced': priced,
           'cost': round(cost or 0, 2), 'lead_cost': round(cost / leads, 2) if leads and cost is not None else None,
           'days': sorted(days.values(), key=lambda d: d['day']),
           'ends': [{'key': key, 'name': OUTCOMES[key], 'count': n} for key, n in ends.items()]}
    if who.role == 'director' and owner:
        out['client_cost'] = charge
    return out


def preset_costs(p: dict, parts: dict, chars_min, tokens_min) -> dict:
    """Оценка минуты разговора для каждого набора робота при нынешнем расходе символов и токенов.
    Синтез — цена ElevenLabs v4, пересчитанная на голос набора; модель — по прайсу набора."""
    from voice import presets
    if chars_min is None or tokens_min is None:
        return {}  # звонков робота ещё не было: оценивать не по чему
    out = {}
    for key, ps in presets.PRESETS.items():
        synth = chars_min / 1000 * presets.tts_rub_1k(ps['tts'], p['eleven_1k_chars'])
        llm = tokens_min / 1000 * presets.LLM[ps['llm']]['price'][0]
        out[key] = round((parts.get('mango') or 0) + (parts.get('asr') or 0) + synth + llm, 2)
    return out


async def economics(pool) -> dict:
    """Для калькулятора директора: сколько стоит нам минута разговора робота и что по каждому клиенту в этом месяце.

    Синтез и модель считаются в символах и токенах, поэтому их расход на минуту берётся из звонков робота
    за последние 30 дней."""
    p = await prices(pool)
    since = datetime.now(timezone.utc) - timedelta(days=CALC_DAYS)
    query = ('SELECT count(*) AS calls, count(*) FILTER (WHERE outcome <> $3) AS reached, '
             'coalesce(sum(duration_s), 0) AS seconds, coalesce(sum(tts_chars), 0) AS chars, '
             'coalesce(sum(llm_tokens), 0) AS tokens FROM robot_calls WHERE at > $1 AND at <= $2')
    now = datetime.now(timezone.utc)
    fresh = dict(await pool.fetchrow(query, max(since, QUICK_SINCE), now, 'ndz'))
    estimate = fresh['seconds'] / 60 < FRESH_MINUTES and since < QUICK_SINCE
    use = fresh
    if estimate:  # новых звонков мало: добавляем старые, их токены — с поправкой на заготовки
        old = await pool.fetchrow(query, since, QUICK_SINCE, 'ndz')
        use = {k: fresh[k] + old[k] for k in fresh}
        use['tokens'] = fresh['tokens'] + old['tokens'] * QUICK_TOKEN_RATIO
        estimate = old['seconds'] > 0
    minutes = use['seconds'] / 60
    chars_min = use['chars'] / minutes if minutes else None
    tokens_min = use['tokens'] / minutes if minutes else None
    parts = {'mango': p['mango_min'],
             'synth': chars_min / 1000 * p['tts_1k_chars'] if chars_min is not None else None,
             'llm': tokens_min / 1000 * p['llm_1k_tokens'] if tokens_min is not None else None}
    parts['asr'] = round(asr_cost(use['seconds']) / minutes, 2) if minutes else round(ASR_15S * 4, 2)
    cost_min = sum(v or 0 for v in parts.values())
    month = datetime.now(ZoneInfo('Europe/Moscow')).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = await pool.fetch('SELECT owner, duration_s, tts_chars, llm_tokens, outcome FROM robot_calls WHERE at >= $1', month)
    clients = []
    for name in await enabled_clients(pool):
        mine = [r for r in rows if r['owner'] == name]
        seconds = sum(r['duration_s'] for r in mine)
        cost = round(sum(call_cost(r, p) for r in mine), 2)
        charge = client_charge(seconds, p, name)
        clients.append({'name': name, 'price': p['client_min'].get(name), 'calls': len(mine), 'minutes': round(seconds / 60, 1),
                        'cost': cost, 'charge': charge, 'profit': None if charge is None else round(charge - cost, 2)})
    reach = use['reached'] / use['calls'] if use['calls'] else None
    return {'cost_min': round(cost_min, 2), 'presets': preset_costs(p, parts, chars_min, tokens_min), 'parts': {k: None if v is None else round(v, 2) for k, v in parts.items()},
            'chars_min': None if chars_min is None else round(chars_min), 'tokens_min': None if tokens_min is None else round(tokens_min),
            'sample_minutes': round(minutes), 'estimate': estimate, 'month': month.date().isoformat(), 'clients': clients,
            'typical': {'calls': use['calls'], 'reach': None if reach is None else round(reach * 100),
                        'minutes_per_talk': round(minutes / use['reached'], 1) if use['reached'] and minutes else None}}
