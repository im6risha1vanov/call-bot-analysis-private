"""Зеркало в Google-таблицу: «Сводка», «По дням», «Звонки», «Перезвоны», «Все звонки», «Память менеджеров».

Оформление в стиле таблицы владельца «Прозвон лидов»: Arial 10, тёмно-синяя
шапка с белым жирным текстом, строки-разделители по датам, итоговая строка
серым, цветные статусы. Содержание своё: разборы и перезвоны бота.

База остаётся источником правды: листы целиком перезаписываются из неё раз
в SHEETS_SYNC_MINUTES, правки в самой таблице не читаются (статус перезвона
меняется кнопками в Telegram). Включается двумя переменными:
GOOGLE_SHEET_ID и GOOGLE_SERVICE_ACCOUNT_FILE (JSON ключ сервисного аккаунта,
которому дан доступ «Редактор» к таблице).
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from reports import fmt_phone
from tools import home_client_id
from . import kitchen, memory
from .callbacks import KIND_LABELS, STATUS_LABELS
from .render import CATEGORY_LABELS, OUTCOME_LABELS

log = logging.getLogger(__name__)

API = 'https://sheets.googleapis.com/v4/spreadsheets/'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SUMMARY_SHEET, DAYS_SHEET, CALLS_SHEET, TASKS_SHEET, ALL_SHEET = 'Сводка', 'По дням', 'Звонки', 'Перезвоны', 'Все звонки'
MEMORY_SHEET = 'Память менеджеров'
PRESENTATIONS_SHEET = 'Презентации'
STALE_SHEETS = ('Клиенты',)  # лист неудачной версии от 07.10, бот убирает его сам
HISTORY_DAYS = int(os.getenv('SHEETS_HISTORY_DAYS', '60'))
SUMMARY_DAYS = 30
SYNC_MINUTES = int(os.getenv('SHEETS_SYNC_MINUTES', '10'))
KIND_NAMES = {'cold_first': 'холодный', 'repeat': 'повторный', 'gatekeeper_only': 'секретарь'}

CALLS_HEADER = ['Дата', 'Менеджер', 'Телефон', 'Компания', 'Итог', 'Результат', 'Где потерян', 'Ключевой момент',
                'Как можно было', 'Собеседник', 'ЛПР', 'Потребность', 'Возражение', 'Следующий шаг', 'Тип звонка', 'ID звонка']
CALLS_WIDTHS = [110, 140, 120, 170, 140, 160, 190, 300, 300, 160, 160, 200, 200, 220, 95, 80]
TASKS_HEADER = ['Дата', 'Время', 'Менеджер', 'Компания', 'Телефон', 'Что', 'Договорённость', 'Статус', 'ID звонка']
TASKS_WIDTHS = [95, 70, 140, 180, 120, 90, 320, 150, 80]
DAYS_HEADER = ['Дата', 'Разговоров', 'С результатом', 'Встреч', 'Перезвонов на дату', 'КП', 'Потеряно с шансом']
DAYS_WIDTHS = [110, 105, 115, 80, 140, 70, 140]
SUMMARY_WIDTHS = [260, 110, 115, 100, 80, 140, 70, 110, 140, 120]
ALL_WIDTHS = [95, 170, 95, 95, 105, 120, 85, 105, 120, 120]
MEMORY_WIDTHS = [170, 60, 110, 110, 90, 110, 110, 420, 320]
PRESENTATIONS_HEADER = ['Дата', 'Менеджер', 'Телефон', 'Компания', 'Дошли до', 'Клиент', 'Где остыл', 'Ключевой момент',
                        'Как можно было', 'Не хватает для договора', 'Что дальше', 'Звонок Mango', 'Запись']
PRESENTATIONS_WIDTHS = [95, 140, 120, 160, 120, 95, 180, 300, 300, 200, 300, 150, 260]
STAGE_LABELS = {'paid': 'оплата', 'contract': 'договор', 'decision_date': 'день решения', 'materials': 'материалы',
                'dmp_meeting': 'разговор с ЛПР', 'none': 'без шага'}
TEMPERATURE_LABELS = {'hot': 'горячий', 'warm': 'тёплый', 'cold': 'холодный', 'lost': 'отказ'}
PRESENTATION_CATEGORIES = {
    'no_discovery': 'не выяснил ситуацию клиента', 'generic_pitch': 'рассказ не про бизнес клиента',
    'price_no_value': 'цена без расчёта пользы', 'objection_unhandled': 'не снято возражение',
    'no_trust': 'не дал доказательств', 'no_dmp': 'не вышел на ЛПР', 'no_next_step': 'нет шага с днём',
    'pushy': 'давил на клиента', 'unanswered_question': 'не ответил на вопрос', 'other': 'другое'}
CONTRACT_LABELS = {'legal_name': 'юрлицо', 'inn': 'ИНН', 'signer': 'кто подписывает', 'service': 'услуга',
                   'package': 'пакет', 'competitors': 'конкуренты', 'region': 'регион', 'start': 'старт',
                   'delivery': 'куда отдавать номера'}
PRESENTATION_CHIPS = {'горячий': ('#D9EAD3', '#274E13'), 'тёплый': ('#FFF2CC', '#7F6000'),
                      'холодный': ('#E1E6EE', '#434343'), 'отказ': ('#F4CCCC', '#990000')}
INCOMING, OUTGOING = 1, 2  # context_type в Mango; 3 — внутренний, в статистику не идёт

# Цвета как в образце: шапка #1F4E78, разделитель #D1D8E0, итог #CCCCCC.
HEADER_FILL, SECTION_FILL, TOTAL_FILL = '#1F4E78', '#D1D8E0', '#CCCCCC'
OUTCOME_STATUS = {'success': 'Результат', 'hot': 'Горячий, потерян', 'normal': 'Потерян, шанс был', 'none': 'Без шанса'}
CHIPS = {  # текст статуса: (фон, цвет текста)
    'Результат': ('#D9EAD3', '#274E13'), 'Горячий, потерян': ('#F4CCCC', '#990000'),
    'Потерян, шанс был': ('#FCE5CD', '#B45F06'), 'Без шанса': ('#E1E6EE', '#434343'),
    'открыт': ('#FFF2CC', '#7F6000'), 'просрочен': ('#F4CCCC', '#990000'), 'выполнен': ('#D9EAD3', '#274E13'),
    'отменён': ('#EFEFEF', '#666666'), 'закрыт новым звонком': ('#E1E6EE', '#434343'),
}


@dataclass
class Table:
    """Лист: строки значений и стиль каждой строки (header, section, title, total, note или None)."""
    title: str
    widths: list[int]
    rows: list[list] = field(default_factory=list)
    styles: list[str | None] = field(default_factory=list)
    frozen: int = 0
    filter_from: int | None = None  # строка шапки, с которой ставится фильтр
    chip_column: int | None = None
    bold_column: int | None = None
    chip_span: int = 1  # сколько колонок подряд от chip_column раскрашиваются
    chips: dict | None = None  # своя палитра статусов вместо CHIPS

    def add(self, values, style=None):
        self.rows.append(list(values))
        self.styles.append(style)

    @property
    def width(self) -> int:
        return len(self.widths)


def enabled() -> bool:
    return bool(os.getenv('GOOGLE_SHEET_ID') and os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE'))


def loads(value):
    return json.loads(value) if isinstance(value, str) else value


def outcome_status(data: dict) -> str:
    return OUTCOME_STATUS['success'] if data.get('success') else OUTCOME_STATUS.get(data.get('chance'), 'Без шанса')


def call_row(record, tz) -> list:
    data = loads(record['data'])
    client = data.get('client') or {}
    moment = data.get('key_moment') or {}
    started = record['call_started_at']
    dmp = client.get('dmp_name') or ('да' if client.get('is_dmp') else '')
    if client.get('dmp_contact'):
        dmp = f"{dmp} {client['dmp_contact']}".strip()
    contact = ', '.join(filter(None, [client.get('contact_name'), client.get('contact_role')]))
    return [started.astimezone(tz).strftime('%d.%m.%Y %H:%M') if started else '', record['manager'] or record['extension'] or '',
            fmt_phone(record['client_number']) or '', client.get('company') or '', outcome_status(data),
            ', '.join(OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or []), CATEGORY_LABELS.get(moment.get('category'), ''),
            moment.get('what_happened') or '', moment.get('say_instead') or '', contact, dmp, client.get('need') or '',
            client.get('objection') or '', ((client.get('next_step') or {}).get('raw') or ''),
            KIND_NAMES.get(data.get('kind'), ''), record['call_id']]


def task_status(record, today: date) -> str:
    if record['status'] == 'open' and record['due_date'] < today:
        return 'просрочен'
    return STATUS_LABELS.get(record['status'], record['status'])


def task_row(record, today: date) -> list:
    return [record['due_date'].strftime('%d.%m.%Y'),
            (record['due_time'].strftime('%H:%M') if record['due_time'] else '') + (' примерно' if record['date_is_estimate'] else ''),
            record['manager'] or record['extension'] or '', record['company'] or '', fmt_phone(record['client_number']) or '',
            KIND_LABELS.get(record['kind'], record['kind']), record['raw'] or '', task_status(record, today), record['call_id']]


def local_day(record, tz) -> date | None:
    started = record['call_started_at']
    return started.astimezone(tz).date() if started else None


def counts(datas: list[dict]) -> dict:
    outcomes = collections.Counter(o for d in datas for o in d.get('outcome') or [])
    lost = [d for d in datas if not d.get('success') and d.get('chance') in {'hot', 'normal'}]
    return {'talks': len(datas), 'success': sum(bool(d.get('success')) for d in datas), 'meeting': outcomes['meeting'],
            'callback': outcomes['callback_date'], 'proposal': outcomes['proposal'], 'dmp': outcomes['dmp_contact'],
            'lost': len(lost), 'hot': sum(d.get('chance') == 'hot' for d in lost),
            'categories': collections.Counter((d.get('key_moment') or {}).get('category') for d in lost)}


def calls_table(calls, tz) -> Table:
    table = Table(CALLS_SHEET, CALLS_WIDTHS, frozen=1, filter_from=0, chip_column=4, bold_column=2)
    table.add(CALLS_HEADER, 'header')
    day = None
    for record in calls:
        current = local_day(record, tz)
        if current != day:
            day = current
            same = sum(local_day(r, tz) == day for r in calls)
            table.add([f"Звонки за {day.strftime('%d.%m.%Y') if day else 'без даты'} · {same}"], 'section')
        table.add(call_row(record, tz))
    return table


def tasks_table(tasks, today: date) -> Table:
    table = Table(TASKS_SHEET, TASKS_WIDTHS, frozen=1, filter_from=0, chip_column=7, bold_column=4)
    table.add(TASKS_HEADER, 'header')
    groups = [('Просрочено', lambda t: t['status'] == 'open' and t['due_date'] < today),
              ('На сегодня', lambda t: t['status'] == 'open' and t['due_date'] == today),
              ('Дальше', lambda t: t['status'] == 'open' and t['due_date'] > today),
              ('Закрыто', lambda t: t['status'] != 'open')]
    for label, test in groups:
        items = [t for t in tasks if test(t)]
        if items:
            table.add([f'{label} · {len(items)}'], 'section')
            for task in items:
                table.add(task_row(task, today))
    return table


def days_table(calls, tz) -> Table:
    table = Table(DAYS_SHEET, DAYS_WIDTHS, frozen=1)
    table.add(DAYS_HEADER, 'header')
    by_day = collections.defaultdict(list)
    for record in calls:
        by_day[local_day(record, tz)].append(loads(record['data']))
    for day in sorted((d for d in by_day if d), reverse=True):
        c = counts(by_day[day])
        table.add([day.strftime('%d.%m.%Y'), c['talks'], c['success'], c['meeting'], c['callback'], c['proposal'], c['lost']])
    return table


def summary_table(calls, tasks, tz, today: date) -> Table:
    table = Table(SUMMARY_SHEET, SUMMARY_WIDTHS)
    since = today - timedelta(days=SUMMARY_DAYS - 1)
    recent = [r for r in calls if (local_day(r, tz) or date.min) >= since]
    by_manager = collections.defaultdict(list)
    for record in recent:
        by_manager[record['manager'] or record['extension'] or 'Менеджер не указан'].append(loads(record['data']))

    table.add([f"Итог по менеджерам за {SUMMARY_DAYS} дней ({since.strftime('%d.%m')}–{today.strftime('%d.%m.%Y')})"], 'title')
    table.add(['Менеджер', 'Разговоров', 'С результатом', 'Конверсия', 'Встреч', 'Перезвонов на дату', 'КП',
               'Контакт ЛПР', 'Потеряно с шансом', 'Из них горячих'], 'header')

    def line(name, c):
        share = f"{round(100 * c['success'] / c['talks'])}%" if c['talks'] else '—'
        return [name, c['talks'], c['success'], share, c['meeting'], c['callback'], c['proposal'], c['dmp'], c['lost'], c['hot']]

    for name, datas in sorted(by_manager.items(), key=lambda kv: -len(kv[1])):
        table.add(line(name, counts(datas)))
    total = counts([loads(r['data']) for r in recent])
    table.add(line('ИТОГО', total), 'total')

    table.add([])
    table.add([f'Где теряем звонки с шансом за {SUMMARY_DAYS} дней'], 'title')
    table.add(['Причина', 'Сколько раз'], 'header')
    categories = total['categories']
    categories.pop(None, None)
    for category, times in categories.most_common():
        table.add([CATEGORY_LABELS.get(category, category), times])
    if not categories:
        table.add(['Потерь с шансом нет', 0])

    open_tasks = [t for t in tasks if t['status'] == 'open']
    table.add([])
    table.add(['Перезвоны'], 'title')
    table.add(['Сейчас', 'Сколько'], 'header')
    for label, value in (('Открыто всего', len(open_tasks)),
                         ('Просрочено', sum(t['due_date'] < today for t in open_tasks)),
                         ('На сегодня', sum(t['due_date'] == today for t in open_tasks))):
        table.add([label, value])
    table.add([])
    table.add([f"Обновлено ботом {datetime.now(tz).strftime('%d.%m.%Y %H:%M')}. Таблица обновляется каждые "
               f'{SYNC_MINUTES} минут из базы бота, ручные правки в ней не сохраняются.'], 'note')
    return table


def all_calls_header(min_seconds: int) -> list[str]:
    return ['Дата', 'Сотрудник', 'Добавочный', 'Исходящих', 'Дозвонились', f'Разговоров от {min_seconds} с',
            'Входящих', 'Пропущено входящих', 'Минут разговора', 'Разобрано ботом']


def all_calls_table(rows, tz, min_seconds: int) -> Table:
    """Все звонки из Mango по дням и сотрудникам, а не только разобранные."""
    table = Table(ALL_SHEET, ALL_WIDTHS, frozen=1, filter_from=0, bold_column=1)
    table.add(all_calls_header(min_seconds), 'header')
    stats = collections.defaultdict(lambda: [0] * 7)
    names = {}
    for record in rows:
        day = local_day(record, tz)
        if not day or record['direction'] not in (INCOMING, OUTGOING):
            continue
        extension = record['extension'] or ''
        names.setdefault(extension, record['employee'] or ('Без сотрудника' if not extension else f'Добавочный {extension}'))
        line = stats[(day, extension)]
        talk = record['duration_seconds'] or 0
        if record['direction'] == OUTGOING:
            line[0] += 1
            line[1] += talk > 0
            line[2] += talk >= min_seconds
        else:
            line[3] += 1
            line[4] += talk == 0
        line[5] += talk
        line[6] += bool(record['reviewed'])
    ordered = sorted(stats.items(), key=lambda kv: (-kv[0][0].toordinal(), -kv[1][0], kv[0][1]))
    for (day, extension), (out, answered, talks, incoming, missed, seconds, reviewed) in ordered:
        table.add([day.strftime('%d.%m.%Y'), names[extension], extension, out, answered, talks, incoming, missed,
                   round(seconds / 60), reviewed])
    if not ordered:
        table.add(['Звонков пока нет'])
    return table


def memory_table(stats: dict, managers: list, tz, now: datetime, cards: dict | None = None) -> Table:
    """Память по менеджерам: неделя к прошлой неделе, слабые места из карточки бота и где теряется результат за 30 дней."""
    cards = cards or {}
    table = Table(MEMORY_SHEET, MEMORY_WIDTHS, bold_column=0)
    pct = lambda acc: f'{memory.share(acc)}%' if acc['talks'] else '—'  # noqa: E731
    table.add(['Память менеджеров · последние 7 дней к предыдущим 7 дням'], 'title')
    table.add(['Менеджер', 'Доб.', 'Разговоров за 7 дн.', 'С результатом', 'Доля', 'Доля пред. 7 дн.', 'Горячих потерь',
               'Частая ошибка за 30 дн.', 'Фокус от бота'], 'header')
    empty = {'week': memory.blank(), 'prev': memory.blank(), 'month': memory.blank(), 'example': {}}
    for m in managers:
        acc = stats.get(m['extension']) or empty
        top = memory.top_category(acc['month']['categories'])
        table.add([m['name'], m['extension'], acc['week']['talks'], acc['week']['success'], pct(acc['week']), pct(acc['prev']),
                   acc['week']['hot'], f"{top['label']} · {top['n']}" if top else '',
                   (cards.get(m['extension']) or {}).get('focus') or ''])
    if cards:
        table.add([])
        table.add(['Слабые места и сильные стороны (карточка бота)'], 'title')
        table.add(['Менеджер', 'Доб.', 'За 30 дн.', 'За 7 дн.', 'За пред. 7 дн.', 'Пример: ID звонка', 'Карточка от',
                   'Слабое место / что получается', 'Как лучше'], 'header')
        for m in managers:
            card = cards.get(m['extension'])
            if not card:
                continue
            day = card['day'].strftime('%d.%m.%Y')
            table.add([f"{m['name']} · фокус: {card.get('focus') or '—'}"
                       + (f" · прошлый фокус: {card['focus_result']}" if card.get('focus_result') else '')], 'section')
            for w in card.get('weak_spots') or []:
                c = w.get('counts') or {}
                text = w['title'] + (f". {w['pattern']}" if w.get('pattern') else '')
                table.add([m['name'], m['extension'], c.get('month', ''), c.get('week', ''), c.get('prev', ''),
                           ', '.join(str(x) for x in w.get('examples') or []), day, 'Слабое место: ' + text, w.get('advice') or ''])
            for g in card.get('strengths') or []:
                table.add([m['name'], m['extension'], '', '', '', ', '.join(str(x) for x in g.get('examples') or []), day,
                           'Получается: ' + g['title'], ''])
            if card.get('trend'):
                table.add([m['name'], m['extension'], '', '', '', '', day, 'Динамика: ' + card['trend'], ''])
    table.add([])
    table.add([f'Где терялся результат за {memory.MONTH} дней, по менеджерам'], 'title')
    table.add(['Менеджер', 'Доб.', 'За 30 дн.', 'За 7 дн.', 'За пред. 7 дн.', 'Пример: ID звонка', '', 'Где терялся результат'], 'header')
    for m in managers:
        acc = stats.get(m['extension']) or empty
        ranked = memory.ranked(acc['month']['categories'])
        if not ranked:
            continue
        table.add([f"{m['name']} · потеряно с шансом {acc['month']['lost']} из {acc['month']['talks']}"], 'section')
        for key, n in ranked:
            table.add([m['name'], m['extension'], n, acc['week']['categories'].get(key, 0), acc['prev']['categories'].get(key, 0),
                       acc['example'].get(key, ''), '', CATEGORY_LABELS.get(key, key)])
    table.add([])
    table.add([f"Обновлено ботом {now.astimezone(tz).strftime('%d.%m.%Y %H:%M')} из разборов звонков. "
               'Карточка бота обновляется раз в день после 19:00. Ольга идёт по своему скрипту и сюда не входит.'], 'note')
    return table


def presentations_table(rows, tz) -> Table:
    """Разборы презентаций (review/presentations.py), новые сверху; без привязки к Mango телефон пустой."""
    table = Table(PRESENTATIONS_SHEET, PRESENTATIONS_WIDTHS, frozen=1, filter_from=0, chip_column=5, bold_column=2,
                  chips=PRESENTATION_CHIPS)
    table.add(PRESENTATIONS_HEADER, 'header')
    for r in rows:
        data = loads(r['data']) or {}
        moment = data.get('key_moment') or {}
        contract = data.get('contract') or {}
        match = loads(r['match']) or {}
        if match.get('by') in ('manual', 'registry'):
            mango = 'телефон указан вручную' if match['by'] == 'manual' else 'телефон из реестра'
        elif r['call_id'] and match.get('by') == 'context':
            # презентация не через Mango: привязана по содержанию к холодному звонку с этим клиентом
            when = r['call_started_at'].astimezone(tz).strftime('%d.%m %H:%M') if r['call_started_at'] else ''
            mango = f'по смыслу: холодный {when}'.strip()
        elif r['call_id']:
            mango = r['call_started_at'].astimezone(tz).strftime('%d.%m %H:%M') if r['call_started_at'] else str(r['call_id'])
        else:
            mango = 'не найден: ' + (match.get('reason') or 'проверить')
            if match.get('context'):
                mango += '; по смыслу: ' + (match['context'].get('reason') or 'не нашёлся')
        when = r['day'].strftime('%d.%m.%Y') if r['day'] else ''
        table.add([when, r['manager'] or r['manager_name'] or '', fmt_phone(r['client_number']) if r['client_number'] else (r['client_contact'] or ''),
                   contract.get('company') or '', STAGE_LABELS.get(data.get('stage_after'), ''),
                   TEMPERATURE_LABELS.get(data.get('temperature'), ''),
                   PRESENTATION_CATEGORIES.get(moment.get('category'), '') if moment else '',
                   moment.get('what_happened') or '', moment.get('say_instead') or '',
                   ', '.join(CONTRACT_LABELS.get(f, f) for f in contract.get('missing') or []),
                   data.get('next_step_advice') or '', mango,
                   f'https://drive.google.com/file/d/{r["file_id"]}/view'])
    return table


async def collect(pool, now: datetime | None = None) -> list[Table]:
    """Листы таблицы бота. Таблица одна на сервер (GOOGLE_SHEET_ID) и наша, поэтому только звонки нашей компании."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=HISTORY_DAYS)
    home = await home_client_id(pool)
    client = await pool.fetchrow('SELECT * FROM clients WHERE id=$1', home) if home is not None else None
    calls = await pool.fetch(
        """SELECT r.call_id, r.extension, r.data, c.client_number, c.call_started_at, cl.timezone,
                  (SELECT full_name FROM employees e WHERE e.client_id=r.client_id AND e.extension=r.extension AND e.role='manager' LIMIT 1) AS manager
           FROM call_reviews r JOIN calls c ON c.id=r.call_id JOIN clients cl ON cl.id=r.client_id
           WHERE r.created_at >= $1 AND (r.data->>'reviewed')::boolean IS TRUE AND r.client_id=$2
           ORDER BY coalesce(c.call_started_at, r.created_at) DESC""", since, home)
    tasks = await pool.fetch(
        """SELECT t.*, (SELECT full_name FROM employees e WHERE e.client_id=t.client_id AND e.extension=t.extension AND e.role='manager' LIMIT 1) AS manager
           FROM callback_tasks t WHERE (t.status='open' OR t.updated_at >= $1) AND t.client_id=$2
           ORDER BY t.due_date, t.due_time NULLS LAST""", since, home)
    tz = ZoneInfo(calls[0]['timezone'] if calls else (client['timezone'] if client else None) or 'Europe/Moscow')
    today = now.astimezone(tz).date()
    min_seconds = (client.get('min_call_seconds') if client else None) or 30
    everything = await pool.fetch(
        """SELECT c.extension, c.direction, c.duration_seconds, c.call_started_at,
                  EXISTS(SELECT 1 FROM call_reviews r WHERE r.call_id=c.id AND (r.data->>'reviewed')::boolean IS TRUE) AS reviewed,
                  (SELECT full_name FROM employees e WHERE e.client_id=c.client_id AND e.extension=c.extension
                   ORDER BY (e.role='manager') DESC LIMIT 1) AS employee
           FROM calls c WHERE c.call_started_at >= $1 AND ($2::int IS NULL OR c.client_id=$2)""",
        since, client['id'] if client else None)
    tables = [summary_table(calls, tasks, tz, today), days_table(calls, tz), calls_table(calls, tz), tasks_table(tasks, today),
              all_calls_table(everything, tz, min_seconds)]
    if client:
        managers = [{'extension': r['extension'], 'name': r['full_name'] or f"Доб. {r['extension']}"} for r in await pool.fetch(
            """SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager' AND extension ~ '^[0-9]+$'
               ORDER BY extension::int""", client['id']) if not kitchen.handles(r['extension'])]
        cards = {}
        if await pool.fetchval("SELECT to_regclass('manager_memory') IS NOT NULL"):
            cards = {r['extension']: {**memory.loads(r['card']), 'day': r['day']} for r in await pool.fetch(
                """SELECT DISTINCT ON (extension) extension, day, card FROM manager_memory WHERE client_id=$1
                   ORDER BY extension, day DESC""", client['id'])}
        tables.append(memory_table(await memory.periods(pool, client['id'], now), managers, tz, now, cards))
        if await pool.fetchval("SELECT to_regclass('presentations') IS NOT NULL"):
            done = await pool.fetch(
                """SELECT p.*, coalesce(c.client_number, p.phone) AS client_number, c.call_started_at,
                          (SELECT full_name FROM employees e WHERE e.client_id=p.client_id AND e.extension=p.extension
                             AND e.role='manager' LIMIT 1) AS manager
                   FROM presentations p LEFT JOIN calls c ON c.id=p.call_id
                   WHERE p.client_id=$1 AND p.status='reviewed' ORDER BY p.day DESC NULLS LAST, p.row_no DESC""", client['id'])
            if done:
                tables.append(presentations_table(done, tz))
    return tables


def token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token


def color(value: str) -> dict:
    value = value.lstrip('#')
    return {'red': int(value[0:2], 16) / 255, 'green': int(value[2:4], 16) / 255, 'blue': int(value[4:6], 16) / 255}


def text(*, bold=False, size=10, fg='#000000') -> dict:
    return {'fontFamily': 'Arial', 'fontSize': size, 'bold': bold, 'foregroundColor': color(fg)}


def cells(sheet_id, row, end_row, end_col, fmt) -> dict:
    return {'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': row, 'endRowIndex': end_row,
                                     'startColumnIndex': 0, 'endColumnIndex': end_col},
                           'cell': {'userEnteredFormat': fmt}, 'fields': 'userEnteredFormat'}}


def layout(table: Table, sheet_id: int, grid_rows: int, grid_cols: int, old_rules: int) -> list[dict]:
    """Оформление листа с нуля: прежние объединения, форматы и правила цвета снимаются."""
    whole = {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': grid_rows, 'startColumnIndex': 0, 'endColumnIndex': grid_cols}
    base = {'textFormat': text(), 'backgroundColor': color('#FFFFFF'), 'verticalAlignment': 'TOP', 'wrapStrategy': 'WRAP'}
    requests = [{'deleteConditionalFormatRule': {'sheetId': sheet_id, 'index': 0}} for _ in range(old_rules)]
    requests += [{'unmergeCells': {'range': whole}},
                 {'repeatCell': {'range': whole, 'cell': {'userEnteredFormat': base}, 'fields': 'userEnteredFormat'}},
                 {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': grid_rows},
                                                'properties': {'pixelSize': 21}, 'fields': 'pixelSize'}},
                 {'updateSheetProperties': {'properties': {'sheetId': sheet_id, 'gridProperties': {'frozenRowCount': table.frozen}},
                                            'fields': 'gridProperties.frozenRowCount'}}]
    for i, width in enumerate(table.widths):
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': i, 'endIndex': i + 1},
            'properties': {'pixelSize': width}, 'fields': 'pixelSize'}})
    if table.bold_column is not None:
        requests.append({'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': len(table.rows),
                                                  'startColumnIndex': table.bold_column, 'endColumnIndex': table.bold_column + 1},
                                        'cell': {'userEnteredFormat': {'textFormat': text(bold=True)}},
                                        'fields': 'userEnteredFormat.textFormat'}})
    for row, style in enumerate(table.styles):
        if style == 'header':
            requests.append(cells(sheet_id, row, row + 1, table.width, {
                'backgroundColor': color(HEADER_FILL), 'textFormat': text(bold=True, fg='#FFFFFF'),
                'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE', 'wrapStrategy': 'WRAP'}))
            requests.append({'updateDimensionProperties': {
                'range': {'sheetId': sheet_id, 'dimension': 'ROWS', 'startIndex': row, 'endIndex': row + 1},
                'properties': {'pixelSize': 33}, 'fields': 'pixelSize'}})
        elif style == 'section':
            requests.append({'mergeCells': {'mergeType': 'MERGE_ALL', 'range': {
                'sheetId': sheet_id, 'startRowIndex': row, 'endRowIndex': row + 1, 'startColumnIndex': 0, 'endColumnIndex': table.width}}})
            requests.append(cells(sheet_id, row, row + 1, table.width, {
                'backgroundColor': color(SECTION_FILL), 'textFormat': text(bold=True, size=11), 'verticalAlignment': 'MIDDLE'}))
        elif style == 'title':
            requests.append(cells(sheet_id, row, row + 1, table.width, {'textFormat': text(bold=True, size=13),
                                                                         'wrapStrategy': 'OVERFLOW_CELL'}))
        elif style == 'total':
            requests.append(cells(sheet_id, row, row + 1, table.width, {
                'backgroundColor': color(TOTAL_FILL), 'textFormat': text(bold=True)}))
        elif style == 'note':
            requests.append(cells(sheet_id, row, row + 1, table.width, {'textFormat': text(fg='#727272'),
                                                                         'wrapStrategy': 'OVERFLOW_CELL'}))
    if table.filter_from is not None and len(table.rows) > table.filter_from:
        requests.append({'setBasicFilter': {'filter': {'range': {
            'sheetId': sheet_id, 'startRowIndex': table.filter_from, 'endRowIndex': len(table.rows),
            'startColumnIndex': 0, 'endColumnIndex': table.width}}}})
    if table.chip_column is not None:
        column = table.chip_column
        for label, (fill, fg) in (table.chips or CHIPS).items():
            requests.append({'addConditionalFormatRule': {'index': 0, 'rule': {
                'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': max(len(table.rows), 2),
                            'startColumnIndex': column, 'endColumnIndex': column + table.chip_span}],
                'booleanRule': {'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': label}]},
                                'format': {'backgroundColor': color(fill), 'textFormat': {'foregroundColor': color(fg), 'bold': True}}}}}})
    return requests


async def write(tables: list[Table], *, http=None, access_token=None) -> None:
    spreadsheet = os.environ['GOOGLE_SHEET_ID']
    access_token = access_token or await asyncio.to_thread(token)
    headers = {'Authorization': 'Bearer ' + access_token}
    async with (http or httpx.AsyncClient(timeout=60)) as client:
        async def batch(requests):
            if not requests:
                return {'replies': []}
            response = await client.post(API + spreadsheet + ':batchUpdate', headers=headers, json={'requests': requests})
            response.raise_for_status()
            return response.json()

        meta = await client.get(API + spreadsheet, headers=headers,
                                params={'fields': 'sheets(properties(sheetId,title,gridProperties),conditionalFormats)'})
        meta.raise_for_status()
        existing = {s['properties']['title']: s for s in meta.json().get('sheets', [])}
        grid = {}
        for index, table in enumerate(tables):
            rows = max(len(table.rows) + 50, 200)
            if table.title not in existing:
                reply = await batch([{'addSheet': {'properties': {
                    'title': table.title, 'index': index, 'gridProperties': {'rowCount': rows, 'columnCount': max(table.width, 10)}}}}])
                sheet_id = reply['replies'][0]['addSheet']['properties']['sheetId']
                grid[table.title] = (sheet_id, rows, max(table.width, 10), 0)
                continue
            props = existing[table.title]['properties']
            have_rows = props['gridProperties']['rowCount']
            have_cols = props['gridProperties']['columnCount']
            grow = []
            if have_rows < len(table.rows) + 1:
                grow.append({'appendDimension': {'sheetId': props['sheetId'], 'dimension': 'ROWS', 'length': rows - have_rows}})
                have_rows = rows
            if have_cols < table.width:
                grow.append({'appendDimension': {'sheetId': props['sheetId'], 'dimension': 'COLUMNS', 'length': table.width - have_cols}})
                have_cols = table.width
            grow.append({'updateSheetProperties': {'properties': {'sheetId': props['sheetId'], 'index': index}, 'fields': 'index'}})
            await batch(grow)
            grid[table.title] = (props['sheetId'], have_rows, have_cols,
                                 len(existing[table.title].get('conditionalFormats', [])))
        await batch([{'deleteSheet': {'sheetId': existing[t]['properties']['sheetId']}} for t in STALE_SHEETS if t in existing])
        # Старые строки-разделители объединены на всю ширину. Если оставить их до записи,
        # значения новой строки на том же месте сохраняются только в первой ячейке.
        await batch([{'unmergeCells': {'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': rows,
                                                 'startColumnIndex': 0, 'endColumnIndex': cols}}}
                     for sheet_id, rows, cols, _ in grid.values()])
        for table in tables:
            response = await client.post(API + spreadsheet + f"/values/'{table.title}':clear", headers=headers, json={})
            response.raise_for_status()
            response = await client.put(API + spreadsheet + f"/values/'{table.title}'!A1", headers=headers,
                                        params={'valueInputOption': 'RAW'}, json={'values': table.rows})
            response.raise_for_status()
        await batch([request for table in tables for request in layout(table, *grid[table.title])])


async def sync(pool) -> None:
    tables = await collect(pool)
    await write(tables)
    log.info('Google-таблица обновлена: %s', ', '.join(f'{t.title} {len(t.rows)}' for t in tables))
