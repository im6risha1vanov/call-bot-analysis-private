"""Перезвоны по договорённостям из разбора.

Задача создаётся, когда разбор нашёл следующий шаг с датой. Утром менеджер
получает список на сегодня (и просроченные), за 30 минут до перезвона с
временем — напоминание. Кнопки меняют статус; новый разговор с тем же номером
сам закрывает открытую задачу.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from reports import esc, fmt_phone
from tools import Forbidden

log = logging.getLogger(__name__)

MORNING_AT = time(9, 0)
REMIND_BEFORE = timedelta(minutes=30)
ACTIONS = {'d': 'done', 'm': 'moved', 'x': 'cancelled'}
STATUS_LABELS = {'open': 'открыт', 'done': 'выполнен', 'cancelled': 'отменён', 'closed_by_call': 'закрыт новым звонком'}
KIND_LABELS = {'meeting': 'Встреча', 'callback': 'Перезвон'}


def parse_time(value) -> time | None:
    m = re.search(r'(\d{1,2})[:.](\d{2})', str(value or ''))
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        return time(int(m[1]), int(m[2]))
    return None


def step(data: dict) -> dict | None:
    """Следующий шаг из разбора, если у него есть дата."""
    if not data.get('reviewed'):
        return None
    raw = ((data.get('client') or {}).get('next_step') or {})
    try:
        due = date.fromisoformat(str(raw.get('date') or ''))
    except ValueError:
        return None
    kind = 'meeting' if raw.get('type') == 'meeting' or 'meeting' in (data.get('outcome') or []) else 'callback'
    return {'kind': kind, 'due_date': due, 'due_time': parse_time(raw.get('time')),
            'date_is_estimate': bool(raw.get('date_is_estimate')), 'raw': raw.get('raw')}


def next_workday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


async def record(conn, call, data: dict) -> int | None:
    """Закрыть открытые задачи по этому номеру и создать новую из разбора."""
    if not data.get('reviewed'):
        return None  # автоответчик или обрыв не выполняет перезвон
    # Перезвон, который менеджер назначил в CRM после этого звонка, важнее
    # разбора: его не закрываем и второй задачей не дублируем.
    started = call.get('call_started_at')
    if call['client_number']:
        await conn.execute(
            """UPDATE callback_tasks SET status='closed_by_call', closed_call_id=$3, updated_at=now()
               WHERE client_id=$1 AND client_number=$2 AND status='open' AND call_id IS DISTINCT FROM $3
                 AND NOT (source='crm' AND $4::timestamptz IS NOT NULL AND created_at >= $4)""",
            call['client_id'], call['client_number'], call['id'], started)
    found = step(data)
    if not found:
        return None
    if call['client_number'] and started and await conn.fetchval(
            """SELECT 1 FROM callback_tasks WHERE client_id=$1 AND client_number=$2 AND status='open'
               AND source='crm' AND created_at >= $3 LIMIT 1""", call['client_id'], call['client_number'], started):
        return None
    return await conn.fetchval(
        """INSERT INTO callback_tasks(client_id,extension,call_id,client_number,company,kind,due_date,due_time,date_is_estimate,raw)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           ON CONFLICT(call_id) DO UPDATE SET kind=EXCLUDED.kind, due_date=EXCLUDED.due_date, due_time=EXCLUDED.due_time,
             date_is_estimate=EXCLUDED.date_is_estimate, raw=EXCLUDED.raw, company=EXCLUDED.company, updated_at=now()
           RETURNING id""",
        call['client_id'], call['extension'], call['id'], call['client_number'],
        (data.get('client') or {}).get('company'), found['kind'], found['due_date'], found['due_time'],
        found['date_is_estimate'], found['raw'])


def line(task, today: date | None = None) -> str:
    when = task['due_date'].strftime('%d.%m')
    if task['due_time']:
        when += ' ' + task['due_time'].strftime('%H:%M')
    if task['date_is_estimate']:
        when += ' (примерно)'
    late = ' · просрочен' if today and task['due_date'] < today else ''
    who = task['company'] or fmt_phone(task['client_number']) or 'клиент'
    phone = fmt_phone(task['client_number']) if task['company'] else None
    text = f"{KIND_LABELS.get(task['kind'], 'Перезвон')} · {when}{late} · {esc(who)}"
    if phone:
        text += f' · {esc(phone)}'
    if task['raw']:
        text += f"\n«{esc(task['raw'])}»"
    return text


def keyboard(tasks) -> InlineKeyboardMarkup:
    numbered = len(tasks) > 1
    rows = []
    for i, task in enumerate(tasks, 1):
        prefix = f'{i}. ' if numbered else ''
        rows.append([InlineKeyboardButton(text=prefix + 'Выполнено', callback_data=f"cb:d:{task['id']}"),
                     InlineKeyboardButton(text=prefix + 'Перенести', callback_data=f"cb:m:{task['id']}"),
                     InlineKeyboardButton(text=prefix + 'Отменить', callback_data=f"cb:x:{task['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def morning_text(tasks, today: date) -> str:
    out = ['<b>Перезвоны на сегодня</b>']
    for i, task in enumerate(tasks, 1):
        out.append(f'{i}. ' + line(task, today))
    return '\n'.join(out)


TASKS_SQL = "SELECT * FROM callback_tasks WHERE client_id=$1 AND status='open'"


async def send_morning(pool, bot, client, now_local: datetime) -> int:
    today = now_local.date()
    tasks = await pool.fetch(TASKS_SQL + ' AND due_date <= $2 ORDER BY due_date, due_time NULLS LAST, id', client['id'], today)
    sent = 0
    for ext in sorted({t['extension'] for t in tasks if t['extension']}):
        chat = await pool.fetchval("SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                                   client['id'], ext)
        mine = [t for t in tasks if t['extension'] == ext][:20]
        if not chat:
            continue
        try:
            await bot.send_message(chat, morning_text(mine, today), parse_mode='HTML', reply_markup=keyboard(mine))
            sent += 1
        except Exception:
            log.exception('утренний список перезвонов не доставлен, доб.=%s', ext)
    return sent


async def send_reminders(pool, bot, client, now_local: datetime) -> int:
    tz = ZoneInfo(client['timezone'])
    tasks = await pool.fetch(TASKS_SQL + ' AND due_date=$2 AND due_time IS NOT NULL AND reminded_at IS NULL',
                             client['id'], now_local.date())
    sent = 0
    for task in tasks:
        due = datetime.combine(task['due_date'], task['due_time'], tzinfo=tz)
        if not (due - REMIND_BEFORE <= now_local < due):
            continue
        claimed = await pool.fetchval('UPDATE callback_tasks SET reminded_at=now() WHERE id=$1 AND reminded_at IS NULL RETURNING id', task['id'])
        chat = await pool.fetchval("SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                                   client['id'], task['extension'])
        if claimed and chat:
            try:
                await bot.send_message(chat, '⏰ Через 30 минут\n' + line(task), parse_mode='HTML', reply_markup=keyboard([task]))
                sent += 1
            except Exception:
                log.exception('напоминание о перезвоне не доставлено, task id=%s', task['id'])
    return sent


async def maybe_send(pool, bot) -> None:
    clients = await pool.fetch('SELECT * FROM clients WHERE processing_enabled = true')
    for client in clients:
        try:
            now_local = datetime.now(ZoneInfo(client['timezone']))
            await send_reminders(pool, bot, client, now_local)
            morning = datetime.combine(now_local.date(), MORNING_AT, tzinfo=now_local.tzinfo)
            if now_local.weekday() >= 5 or not (morning <= now_local < morning + timedelta(hours=2)):
                continue
            claimed = await pool.fetchval(
                """INSERT INTO callback_digest_state(client_id,last_morning_date) VALUES($1,$2)
                   ON CONFLICT(client_id) DO UPDATE SET last_morning_date=EXCLUDED.last_morning_date
                   WHERE callback_digest_state.last_morning_date IS DISTINCT FROM EXCLUDED.last_morning_date
                   RETURNING client_id""", client['id'], now_local.date())
            if claimed:
                await send_morning(pool, bot, client, now_local)
        except Exception:
            log.exception('ошибка перезвонов, client id=%s', client['id'])


async def change(pool, actor, task_id: int, action: str) -> str:
    task = await pool.fetchrow('SELECT * FROM callback_tasks WHERE id=$1', task_id)
    if actor is None or task is None or task['client_id'] != actor.client_id or \
            (actor.role == 'manager' and task['extension'] != actor.extension):
        raise Forbidden('Перезвон недоступен.')
    if task['status'] != 'open':
        return f"Уже {STATUS_LABELS.get(task['status'], task['status'])}."
    if action == 'moved':
        tz = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', task['client_id']))
        day = next_workday(max(task['due_date'], datetime.now(tz).date()))
        await pool.execute('UPDATE callback_tasks SET due_date=$2, reminded_at=NULL, updated_at=now() WHERE id=$1', task_id, day)
        return f"Перенесён на {day.strftime('%d.%m')}."
    await pool.execute('UPDATE callback_tasks SET status=$2, updated_at=now() WHERE id=$1', task_id, action)
    return 'Отмечен как выполненный.' if action == 'done' else 'Отменён.'
