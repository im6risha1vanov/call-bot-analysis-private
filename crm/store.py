"""Данные CRM менеджера: очередь обзвона, база номеров, карточка клиента, итоги.

Менеджер видит только свой добавочный: его берём из сессии. Руководитель и
владелец выбирают менеджера, но звонить с чужого добавочного не могут.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from asyncpg import UniqueViolationError

from tools import Forbidden, is_privileged
from .dial import normalize

OUTCOMES = {
    'no_answer': 'Не взял трубку',
    'callback': 'Перезвонить',
    'interested': 'Интерес, думает',
    'proposal': 'Отправил КП',
    'meeting': 'Назначил встречу',
    'not_interested': 'Отказ',
    'bad_number': 'Неверный номер',
}
NEEDS_DATE = {'callback', 'meeting'}
LEAD_STATUS = {'new': 'Новый', **OUTCOMES}
KIND_LABELS = {'meeting': 'Встреча', 'callback': 'Перезвон'}
RETRY_AFTER = timedelta(hours=2)
MAX_ATTEMPTS = 3
MAX_LINES = 5000
INCOMING, OUTGOING = 1, 2
PHONE = re.compile(r'\+?\d[\d\s\-()]{8,16}\d')
PENDING_LEAD = f"""(l.status='new' OR (l.status='no_answer' AND l.attempts < {MAX_ATTEMPTS}))
    AND NOT EXISTS (SELECT 1 FROM callback_tasks t WHERE t.client_id=l.client_id
                    AND t.client_number=l.number AND t.status='open')"""


async def zone(pool, client_id: int) -> ZoneInfo:
    return ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow')


async def team(pool, client_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager' AND archived_at IS NULL
           AND (extension ~ '^[0-9]+$' OR extension LIKE 'crm:%')
           ORDER BY CASE WHEN extension ~ '^[0-9]+$' THEN extension::numeric END, full_name, extension""", client_id)
    return [{'ext': r['extension'], 'name': r['full_name'] or f"Доб. {r['extension']}"} for r in rows]


async def scope(pool, actor, ext: str | None) -> str:
    """Чей добавочный смотрим. Менеджеру всегда свой."""
    if actor is None:
        raise Forbidden('Нужно войти.')
    if not is_privileged(actor):
        if not actor.extension or not (actor.extension.isdigit() or actor.extension.startswith('crm:')):
            raise Forbidden('У вас нет добавочного в Mango.')
        return actor.extension
    people = await team(pool, actor.client_id)
    if not people:
        raise Forbidden('В отделе нет менеджеров.')
    if ext is None:
        return people[0]['ext']
    if ext not in {p['ext'] for p in people}:
        raise Forbidden('Такого менеджера нет.')
    return ext


def variants(number: str) -> list[str]:
    """Как номер может лежать в calls: Mango отдаёт 7XXXXXXXXXX, но на всякий случай."""
    return [number, '+' + number, '8' + number[1:]]


def fmt_dt(value: datetime | None, tz: ZoneInfo) -> str | None:
    return value.astimezone(tz).strftime('%Y-%m-%dT%H:%M') if value else None


def task_item(t, today: date) -> dict:
    return {'kind': 'task', 'id': t['id'], 'number': t['client_number'],
            'title': t['company'] or t.get('lead_name') or '', 'note': t['raw'] or '',
            'label': KIND_LABELS.get(t['kind'], 'Перезвон'), 'source': t['source'],
            'due_date': t['due_date'].isoformat(), 'due_time': t['due_time'].strftime('%H:%M') if t['due_time'] else None,
            'estimate': t['date_is_estimate'], 'late': t['due_date'] < today}


def lead_item(r) -> dict:
    return {'kind': 'lead', 'id': r['id'], 'number': r['number'], 'title': ' · '.join(x for x in (r['name'], r['company']) if x),
            'name': r['name'], 'company': r['company'], 'note': r['note'] or '', 'source': r['source'] or '',
            'status': r['status'], 'label': LEAD_STATUS.get(r['status'], r['status']), 'attempts': r['attempts'],
            'version': r['updated_at'].isoformat(), 'can_edit_number': r['status'] == 'new' and r['attempts'] == 0}


TASK_SQL = """SELECT t.*, (SELECT l.name FROM crm_leads l WHERE l.client_id=t.client_id AND l.extension=t.extension
                         AND l.number=t.client_number LIMIT 1) AS lead_name
              FROM callback_tasks t WHERE t.client_id=$1 AND t.extension=$2 AND t.status='open'"""


async def queue(pool, client_id: int, ext: str, now: datetime | None = None) -> dict:
    tz = await zone(pool, client_id)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    tasks = await pool.fetch(
        TASK_SQL + """ AND t.client_number IS NOT NULL AND t.due_date <= $3 AND (t.retry_at IS NULL OR t.retry_at <= $4)
                   ORDER BY t.due_date, t.due_time NULLS LAST, t.id""", client_id, ext, today, now)
    leads = await pool.fetch(
        f"""SELECT * FROM crm_leads l WHERE client_id=$1 AND extension=$2
             AND {PENDING_LEAD} AND (l.status='new' OR l.next_at IS NULL OR l.next_at <= $3)
           ORDER BY (status='no_answer') DESC, coalesce(next_at, created_at), id LIMIT 300""",
        client_id, ext, now)
    later = await pool.fetchval(
        """SELECT (SELECT count(*) FROM callback_tasks WHERE client_id=$1 AND extension=$2 AND status='open'
                     AND (due_date > $3 OR retry_at > $4))
                + (SELECT count(*) FROM crm_leads WHERE client_id=$1 AND extension=$2 AND status='no_answer'
                     AND attempts < $5 AND next_at > $4)""", client_id, ext, today, now, MAX_ATTEMPTS)
    seen, items = set(), []
    for t in tasks:
        if t['client_number'] not in seen:
            seen.add(t['client_number'])
            items.append(task_item(t, today))
    items += [lead_item(r) for r in leads if r['number'] not in seen]
    return {'items': items, 'later': later, 'today': today.isoformat()}


async def tasks(pool, client_id: int, ext: str) -> dict:
    tz = await zone(pool, client_id)
    today = datetime.now(tz).date()
    rows = await pool.fetch(TASK_SQL + ' ORDER BY t.due_date, t.due_time NULLS LAST, t.id LIMIT 500', client_id, ext)
    return {'items': [task_item(t, today) for t in rows], 'today': today.isoformat()}


async def change_task(pool, actor, client_id: int, ext: str, task_id: int, action: str,
                      due_date: date | None = None, due_time: time | None = None) -> None:
    task = await pool.fetchrow(
        'SELECT status, updated_at FROM callback_tasks WHERE id=$1 AND client_id=$2 AND extension=$3',
        task_id, client_id, ext)
    if task is None:
        raise Forbidden('Перезвон недоступен.')
    if task['status'] != 'open':
        raise ValueError('Перезвон уже закрыт.')
    if action == 'move':
        tz = await zone(pool, client_id)
        if due_date is None or due_date < datetime.now(tz).date():
            raise ValueError('Выберите дату не раньше сегодняшней.')
        changed = await pool.execute(
            """UPDATE callback_tasks SET due_date=$2, due_time=$3, date_is_estimate=false, retry_at=NULL,
               reminded_at=NULL, updated_at=now()
               WHERE id=$1 AND client_id=$4 AND extension=$5 AND status='open' AND updated_at=$6""",
            task_id, due_date, due_time, client_id, ext, task['updated_at'])
    elif action in ('done', 'cancelled'):
        changed = await pool.execute(
            """UPDATE callback_tasks SET status=$2, updated_at=now()
               WHERE id=$1 AND client_id=$3 AND extension=$4 AND status='open' AND updated_at=$5""",
            task_id, action, client_id, ext, task['updated_at'])
    else:
        raise ValueError('Неизвестное действие.')
    if changed != 'UPDATE 1':
        raise ValueError('Перезвон уже закрыт или изменён. Обновите список.')


def parse_leads(text: str) -> tuple[list[dict], list[str]]:
    """Строки «номер; имя; компания; заметка» в любом порядке полей.

    Номер находим в строке сам, остальное делим по ; таб | и запятой:
    первое — имя, второе — компания, остальное — заметка."""
    found, bad = [], []
    for raw in (text or '').splitlines()[:MAX_LINES]:
        line = raw.strip()
        if not line:
            continue
        m = PHONE.search(line)
        number = normalize(m[0]) if m else None
        if number is None:
            bad.append(line[:80])
            continue
        rest = (line[:m.start()] + '\t' + line[m.end():])
        parts = [p.strip(' "\'') for p in re.split(r'[;\t|,]', rest)]
        parts = [p for p in parts if p]
        found.append({'number': number, 'name': (parts[0] if parts else None),
                      'company': (parts[1] if len(parts) > 1 else None),
                      'note': ('; '.join(parts[2:]) if len(parts) > 2 else None)})
    return found, bad


async def add_leads(pool, client_id: int, ext: str, text: str, source: str | None) -> dict:
    found, bad = parse_leads(text)
    return await insert_leads(pool, client_id, ext, found, bad, source)


def lead_text(value, limit: int) -> str | None:
    return str(value or '').replace('\x00', '').strip()[:limit] or None


async def insert_leads(pool, client_id: int, ext: str, found: list[dict], bad: list[str], source) -> dict:
    unique: dict[str, dict] = {}
    for f in found:  # повтор номера в той же вставке дополняет пустые поля первого
        first = unique.setdefault(f['number'], f)
        for key in ('name', 'company', 'note'):
            first[key] = first[key] or f[key]
    added = await pool.fetchval(
        """WITH ins AS (
             INSERT INTO crm_leads(client_id,extension,number,name,company,note,source)
             SELECT $1,$2,number,name,company,note,$7
             FROM unnest($3::text[],$4::text[],$5::text[],$6::text[]) WITH ORDINALITY AS incoming(number,name,company,note,ord)
             ORDER BY ord
             ON CONFLICT (client_id,extension,number) DO NOTHING RETURNING 1
           ) SELECT count(*) FROM ins""",
        client_id, ext, list(unique),
        [lead_text(f['name'], 120) for f in unique.values()],
        [lead_text(f['company'], 120) for f in unique.values()],
        [lead_text(f['note'], 500) for f in unique.values()], lead_text(source, 80)) if unique else 0
    return {'added': added, 'duplicates': len(found) - added, 'invalid': len(bad), 'invalid_samples': bad[:5]}


async def leads(pool, client_id: int, ext: str, status: str | None = None, *, pending: bool = False,
                before: int | None = None) -> dict:
    where = 'l.client_id=$1 AND l.extension=$2'
    snapshot = None
    if pending:
        where += ' AND ' + PENDING_LEAD
        snapshot = (await pool.fetchval('SELECT clock_timestamp()')).isoformat()
    counts = {r['status']: r['n'] for r in await pool.fetch(
        f'SELECT status, count(*) AS n FROM crm_leads l WHERE {where} GROUP BY status', client_id, ext)}
    next_page = None
    if pending:
        rows = await pool.fetch(
            f"""SELECT * FROM crm_leads l WHERE {where} AND ($3::bigint IS NULL OR id<$3)
               ORDER BY id DESC LIMIT 101""", client_id, ext, before)
        if len(rows) > 100:
            rows = rows[:100]
            next_page = str(rows[-1]['id'])
    else:
        rows = await pool.fetch(
            f"""SELECT * FROM crm_leads l WHERE {where} AND ($3::text IS NULL OR status=$3)
               ORDER BY updated_at DESC, id DESC LIMIT 500""", client_id, ext, status)
    sources = [r['source'] for r in await pool.fetch(
        """SELECT source, count(*) FROM crm_leads WHERE client_id=$1 AND extension=$2 AND source IS NOT NULL
           GROUP BY source ORDER BY max(created_at) DESC LIMIT 20""", client_id, ext)]
    return {'items': [lead_item(r) for r in rows], 'counts': counts, 'total': sum(counts.values()),
            'labels': LEAD_STATUS, 'sources': sources, 'snapshot': snapshot, 'next': next_page}


async def update_lead(pool, client_id: int, ext: str, lead_id: int, version: datetime, data: dict) -> None:
    number = normalize(data.get('number'))
    if number is None:
        raise ValueError('Укажите корректный номер телефона.')
    async with pool.acquire() as conn, conn.transaction():
        current = await conn.fetchrow(
            'SELECT * FROM crm_leads WHERE id=$1 AND client_id=$2 AND extension=$3 FOR UPDATE', lead_id, client_id, ext)
        if current is None:
            raise Forbidden('Номер не найден.')
        if current['updated_at'] != version:
            raise ValueError('Контакт уже изменён. Обновите список и попробуйте снова.')
        if number != current['number']:
            used = current['status'] != 'new' or current['attempts'] or await conn.fetchval(
                """SELECT EXISTS (SELECT 1 FROM crm_dials WHERE client_id=$1 AND extension=$2 AND number=$3)
                       OR EXISTS (SELECT 1 FROM callback_tasks WHERE client_id=$1 AND client_number=$3 AND status='open')""",
                client_id, ext, current['number'])
            if used:
                raise ValueError('У контакта уже есть звонок или перезвон. Можно изменить его данные, сохранив телефон.')
        try:
            await conn.execute(
                """UPDATE crm_leads SET number=$2,name=$3,company=$4,note=$5,source=$6,updated_at=clock_timestamp()
                   WHERE id=$1""", lead_id, number, lead_text(data.get('name'), 120), lead_text(data.get('company'), 120),
                lead_text(data.get('note'), 500), lead_text(data.get('source'), 80))
        except UniqueViolationError:
            raise ValueError('Этот номер уже есть в вашей базе.') from None


async def clear_queue(pool, client_id: int, ext: str, before: datetime) -> int:
    return await pool.fetchval(
        f"""WITH removed AS (
              DELETE FROM crm_leads l WHERE l.client_id=$1 AND l.extension=$2
                AND l.updated_at <= least($3, clock_timestamp()) AND {PENDING_LEAD} RETURNING 1
            ) SELECT count(*) FROM removed""", client_id, ext, before)


async def delete_lead(pool, client_id: int, ext: str, lead_id: int, version: datetime | None = None) -> None:
    where = 'l.id=$1 AND l.client_id=$2 AND l.extension=$3'
    args = (lead_id, client_id, ext)
    if version is not None:
        where += ' AND l.updated_at=$4 AND ' + PENDING_LEAD
        args += (version,)
    done = await pool.fetchval(f'DELETE FROM crm_leads l WHERE {where} RETURNING id', *args)
    if done is None:
        raise Forbidden('Номер не найден.')


def review_view(data, own: bool) -> dict | None:
    if not data:
        return None
    data = json.loads(data) if isinstance(data, str) else data
    if not data.get('reviewed', True):
        return None
    view = {'success': bool(data.get('success')), 'chance': data.get('chance'), 'rop_line': data.get('rop_line')}
    if own:
        view['manager_text'] = data.get('manager_text')
    return view


async def card(pool, actor, client_id: int, ext: str, number) -> dict:
    target = normalize(number)
    if target is None:
        raise ValueError('Номер не распознан.')
    tz = await zone(pool, client_id)
    names = {r['extension']: r['full_name'] for r in await pool.fetch(
        'SELECT extension, full_name FROM employees WHERE client_id=$1', client_id)}
    privileged = is_privileged(actor)
    calls = await pool.fetch(
        """SELECT c.id, c.extension, c.direction, c.duration_seconds, c.call_started_at, r.data,
                  c.recording_id IS NOT NULL AS rec
           FROM calls c LEFT JOIN call_reviews r ON r.call_id=c.id
           WHERE c.client_id=$1 AND c.client_number = ANY($2::text[])
           ORDER BY c.call_started_at DESC NULLS LAST LIMIT 40""", client_id, variants(target))
    outcomes = await pool.fetch(
        """SELECT * FROM crm_outcomes WHERE client_id=$1 AND number=$2 ORDER BY created_at DESC LIMIT 40""",
        client_id, target)
    open_tasks = await pool.fetch(
        """SELECT * FROM callback_tasks WHERE client_id=$1 AND client_number = ANY($2::text[]) AND status='open'
           ORDER BY due_date, due_time NULLS LAST""", client_id, variants(target))
    lead = await pool.fetchrow('SELECT * FROM crm_leads WHERE client_id=$1 AND extension=$2 AND number=$3',
                               client_id, ext, target)
    today = datetime.now(tz).date()
    company = next((t['company'] for t in open_tasks if t['company']), None)
    return {
        'number': target,
        'lead': lead_item(lead) if lead else None,
        'company': company,
        'tasks': [{**task_item(t, today), 'ext': t['extension'], 'who': names.get(t['extension'])} for t in open_tasks],
        'calls': [{'id': c['id'], 'at': fmt_dt(c['call_started_at'], tz), 'ext': c['extension'],
                   'who': names.get(c['extension']) or (f"Доб. {c['extension']}" if c['extension'] else 'Без сотрудника'),
                   'incoming': c['direction'] == INCOMING, 'seconds': c['duration_seconds'] or 0,
                   # Запись чужого звонка менеджер не слушает.
                   'rec': c['rec'] and (privileged or c['extension'] == ext),
                   # Менеджеру разбор чужого звонка не показываем, только кто и когда звонил.
                   'review': review_view(c['data'], c['extension'] == ext) if privileged or c['extension'] == ext else None}
                  for c in calls],
        'outcomes': [{'at': fmt_dt(o['created_at'], tz), 'ext': o['extension'], 'who': names.get(o['extension']),
                      'outcome': o['outcome'], 'label': OUTCOMES.get(o['outcome'], o['outcome']), 'comment': o['comment'],
                      'due_date': o['due_date'].isoformat() if o['due_date'] else None,
                      'due_time': o['due_time'].strftime('%H:%M') if o['due_time'] else None} for o in outcomes],
    }


async def outcome(pool, actor, client_id: int, ext: str, number, kind: str, comment: str | None = None,
                  due_date: date | None = None, due_time: time | None = None, dial_id: int | None = None) -> dict:
    if kind not in OUTCOMES:
        raise ValueError('Неизвестный итог.')
    target = normalize(number)
    if target is None:
        raise ValueError('Номер не распознан.')
    tz = await zone(pool, client_id)
    today = datetime.now(tz).date()
    if kind in NEEDS_DATE and due_date is None:
        raise ValueError('Укажите, когда перезвонить или когда встреча.')
    if kind in ('no_answer', 'not_interested', 'bad_number'):
        due_date = due_time = None
    if due_date and due_date < today:
        raise ValueError('Дата уже прошла.')
    if due_date is None:
        due_time = None
    comment = (comment or '').strip()[:1000] or None
    async with pool.acquire() as conn, conn.transaction():
        if dial_id is not None and not await conn.fetchval(
                'SELECT 1 FROM crm_dials WHERE id=$1 AND client_id=$2 AND extension=$3 AND number=$4 FOR UPDATE',
                dial_id, client_id, ext, target):
            dial_id = None
        # Keep the last result of this dial when the browser repeats an identical save.
        if dial_id is not None:
            previous = await conn.fetchrow(
                """SELECT outcome, comment, due_date, due_time, callback_task_id FROM crm_outcomes
                   WHERE client_id=$1 AND number=$2 AND extension=$3 AND dial_id=$4
                   ORDER BY id DESC LIMIT 1""", client_id, target, ext, dial_id)
            if previous is not None and (
                previous['outcome'], previous['comment'], previous['due_date'], previous['due_time']
            ) == (kind, comment, due_date, due_time):
                return {'task_id': previous['callback_task_id']}
        lead = await conn.fetchrow('SELECT * FROM crm_leads WHERE client_id=$1 AND extension=$2 AND number=$3 FOR UPDATE',
                                   client_id, ext, target)
        task_id = None
        if kind == 'no_answer':
            await conn.execute(
                """UPDATE callback_tasks SET retry_at=now()+$4::interval, updated_at=now()
                   WHERE client_id=$1 AND extension=$2 AND client_number=$3 AND status='open'""",
                client_id, ext, target, RETRY_AFTER)
            if lead:
                await conn.execute(
                    """UPDATE crm_leads SET attempts=attempts+1, next_at=now()+$2::interval, updated_at=now(),
                       status=CASE WHEN status IN ('new','no_answer') THEN 'no_answer' ELSE status END WHERE id=$1""",
                    lead['id'], RETRY_AFTER)
        else:
            company = await conn.fetchval(
                """SELECT company FROM callback_tasks WHERE client_id=$1 AND client_number=$2 AND company IS NOT NULL
                   ORDER BY id DESC LIMIT 1""", client_id, target) or (lead['company'] if lead else None)
            await conn.execute(
                """UPDATE callback_tasks SET status='done', retry_at=NULL, updated_at=now()
                   WHERE client_id=$1 AND extension=$2 AND client_number=$3 AND status='open'""", client_id, ext, target)
            if due_date:
                task_id = await conn.fetchval(
                    """INSERT INTO callback_tasks(client_id,extension,call_id,client_number,company,kind,due_date,due_time,raw,source)
                       VALUES($1,$2,NULL,$3,$4,$5,$6,$7,$8,'crm') RETURNING id""",
                    client_id, ext, target, company, 'meeting' if kind == 'meeting' else 'callback',
                    due_date, due_time, comment)
            if lead:
                await conn.execute(
                    'UPDATE crm_leads SET status=$2, attempts=0, next_at=NULL, updated_at=now() WHERE id=$1', lead['id'], kind)
        await conn.execute(
            """INSERT INTO crm_outcomes(client_id,extension,number,outcome,comment,due_date,due_time,dial_id,
                                        telegram_user_id,callback_task_id)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            client_id, ext, target, kind, comment, due_date, due_time, dial_id, actor.telegram_user_id, task_id)
    return {'task_id': task_id}


async def my_calls(pool, actor, client_id: int, ext: str, days: int = 7) -> dict:
    tz = await zone(pool, client_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await pool.fetch(
        """SELECT c.id, c.direction, c.duration_seconds, c.call_started_at, c.client_number, r.data,
                  c.recording_id IS NOT NULL AS rec
           FROM calls c LEFT JOIN call_reviews r ON r.call_id=c.id
           WHERE c.client_id=$1 AND c.extension=$2 AND c.call_started_at >= $3
           ORDER BY c.call_started_at DESC LIMIT 400""", client_id, ext, since)
    return {'items': [{'id': c['id'], 'at': fmt_dt(c['call_started_at'], tz), 'number': c['client_number'],
                       'incoming': c['direction'] == INCOMING, 'seconds': c['duration_seconds'] or 0,
                       'rec': c['rec'], 'review': review_view(c['data'], True)} for c in rows]}


async def stats(pool, client_id: int, ext: str | None, days: int = 7) -> dict:
    """По дням и менеджерам: звонки из Mango и итоги, отмеченные в CRM."""
    tz = await zone(pool, client_id)
    today = datetime.now(tz).date()
    start = datetime.combine(today - timedelta(days=days - 1), time(0), tzinfo=tz)
    min_seconds = await pool.fetchval('SELECT min_call_seconds FROM clients WHERE id=$1', client_id) or 30
    rows = await pool.fetch(
        """SELECT (call_started_at AT TIME ZONE $4)::date AS day, extension,
                  count(*) FILTER (WHERE direction=2) AS dials,
                  count(*) FILTER (WHERE direction=2 AND duration_seconds>0) AS answered,
                  count(*) FILTER (WHERE direction=2 AND duration_seconds >= $5) AS talks,
                  count(*) FILTER (WHERE direction=1) AS incoming,
                  count(*) FILTER (WHERE direction=1 AND coalesce(duration_seconds,0)=0) AS missed,
                  coalesce(sum(duration_seconds),0) AS seconds
           FROM calls WHERE client_id=$1 AND call_started_at >= $2 AND ($3::text IS NULL OR extension=$3)
             AND extension IS NOT NULL
           GROUP BY 1, 2""", client_id, start, ext, str(tz), min_seconds)
    marks = await pool.fetch(
        """SELECT (created_at AT TIME ZONE $4)::date AS day, extension, outcome, count(*) AS n
           FROM crm_outcomes WHERE client_id=$1 AND created_at >= $2 AND ($3::text IS NULL OR extension=$3)
           GROUP BY 1, 2, 3""", client_id, start, ext, str(tz))
    pressed = await pool.fetch(
        """SELECT (created_at AT TIME ZONE $4)::date AS day, extension, count(*) AS n
           FROM crm_dials WHERE client_id=$1 AND created_at >= $2 AND ($3::text IS NULL OR extension=$3) AND result=1000
           GROUP BY 1, 2""", client_id, start, ext, str(tz))
    out: dict[tuple, dict] = {}

    def row(day, e):
        return out.setdefault((day, e), {'day': day.isoformat(), 'ext': e, 'dials': 0, 'answered': 0, 'talks': 0,
                                         'incoming': 0, 'missed': 0, 'seconds': 0, 'crm_dials': 0, 'outcomes': {}})
    for r in rows:
        row(r['day'], r['extension']).update({k: int(r[k]) for k in ('dials', 'answered', 'talks', 'incoming', 'missed', 'seconds')})
    for r in marks:
        row(r['day'], r['extension'])['outcomes'][r['outcome']] = int(r['n'])
    for r in pressed:
        row(r['day'], r['extension'])['crm_dials'] = int(r['n'])
    return {'rows': sorted(out.values(), key=lambda x: (x['day'], x['ext'])), 'min_seconds': min_seconds,
            'today': today.isoformat(), 'labels': OUTCOMES}
