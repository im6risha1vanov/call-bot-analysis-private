"""Хранение разбора (call_reviews) и доставка: сразу и в вечерней сводке."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from tools import Forbidden, report_chat_ids
from . import engine, render

log = logging.getLogger(__name__)

ROW_SQL = """
SELECT r.call_id, r.client_id, r.extension, r.data, r.manager_sent_at, r.rop_sent_at,
       c.client_number, c.call_started_at, c.duration_seconds, cl.timezone,
       (SELECT full_name FROM employees e WHERE e.client_id=r.client_id AND e.extension=r.extension
         AND e.role='manager' LIMIT 1) AS manager
FROM call_reviews r JOIN calls c ON c.id=r.call_id JOIN clients cl ON cl.id=r.client_id
"""


def unpack(record) -> dict:
    data = record['data']
    data = json.loads(data) if isinstance(data, str) else dict(data)
    started = record['call_started_at']
    if started is not None:
        started = started.astimezone(ZoneInfo(record['timezone']))
    meta = {'manager': record['manager'], 'client_number': record['client_number'],
            'call_started_at': started, 'duration_seconds': record['duration_seconds']}
    return {'call_id': record['call_id'], 'client_id': record['client_id'], 'extension': record['extension'],
            'data': data, 'meta': meta, 'manager_sent_at': record['manager_sent_at'], 'rop_sent_at': record['rop_sent_at']}


async def previous(pool, client_id: int, client_number, before_call_id: int) -> dict | None:
    """Итог прошлого разбора этому же номеру: контекст для повторного звонка."""
    if not client_number:
        return None
    row = await pool.fetchrow(
        """SELECT c.call_started_at, r.data->>'rop_line' AS rop_line FROM call_reviews r JOIN calls c ON c.id=r.call_id
           WHERE r.client_id=$1 AND c.client_number=$2 AND r.call_id<>$3 ORDER BY c.call_started_at DESC LIMIT 1""",
        client_id, client_number, before_call_id)
    return {'date': row['call_started_at'].date().isoformat(), 'rop_line': row['rop_line']} if row else None


async def save(conn, call, data: dict, cost: float) -> None:
    moment = data.get('key_moment') or {}
    await conn.execute(
        """INSERT INTO call_reviews(call_id,client_id,extension,prompt_version,kind,success,chance,category,data,cost_units)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
           ON CONFLICT(call_id) DO UPDATE SET prompt_version=EXCLUDED.prompt_version, kind=EXCLUDED.kind,
             success=EXCLUDED.success, chance=EXCLUDED.chance, category=EXCLUDED.category, data=EXCLUDED.data,
             cost_units=EXCLUDED.cost_units""",
        call['id'], call['client_id'], call['extension'], engine.PROMPT_VERSION, data.get('kind'),
        bool(data.get('success')), data.get('chance'), moment.get('category'),
        json.dumps(data, ensure_ascii=False), cost)


async def load(pool, call_id: int) -> dict | None:
    row = await pool.fetchrow(ROW_SQL + ' WHERE r.call_id=$1', call_id)
    return unpack(row) if row else None


async def access(pool, actor, call_id: int) -> dict:
    row = await load(pool, call_id)
    if actor is None or row is None or row['client_id'] != actor.client_id:
        raise Forbidden('Разбор недоступен.')
    if actor.role == 'manager' and row['extension'] != actor.extension:
        raise Forbidden('Разбор недоступен.')
    return row


async def train_assignment(pool, actor, call_id: int) -> int:
    """Назначение в тренажёр по ключевому моменту своего звонка (только сам менеджер)."""
    row = await access(pool, actor, call_id)
    if actor.role != 'manager' or not row['data'].get('key_moment'):
        raise Forbidden('Тренировка по этому звонку недоступна.')
    return await pool.fetchval(
        """INSERT INTO pending_train_assignments(client_id, extension, topic, mode, assigned_by_telegram_user_id)
           VALUES($1,$2,$3,'dialog',$4) RETURNING id""",
        actor.client_id, actor.extension, f'review:{call_id}', actor.telegram_user_id)


def env_copy_tokens() -> list[str]:
    return [t for t in (t.strip() for t in os.getenv('REVIEW_COPY_TO', '').split(',')) if t]


async def copy_tokens(pool, client_id: int) -> tuple[list[str], str]:
    """Список получателей копий и откуда он: 'crm' (раздел «Сотрудники») или 'env' (REVIEW_COPY_TO)."""
    try:
        tokens = await pool.fetchval('SELECT tokens FROM review_copy_lists WHERE client_id=$1', client_id)
    except Exception as exc:
        if exc.__class__.__name__ != 'UndefinedTableError':
            raise
        tokens = None  # до миграции 019
    return (list(tokens), 'crm') if tokens is not None else (env_copy_tokens(), 'env')


async def copy_chat_ids(pool, client_id: int) -> list[int]:
    """Кому уходит полная копия каждого разбора: список из CRM, а пока его нет, REVIEW_COPY_TO через запятую.

    Число — Telegram ID. Иначе сотрудник бота по добавочному или имени
    (`head:marozeto`, `@marozeto`): его ID берётся из employees, когда он нажмёт
    /start; до этого он просто пропускается."""
    ids = []
    for token in (await copy_tokens(pool, client_id))[0]:
        if re.fullmatch(r'-?\d+', token):
            ids.append(int(token))
        elif token:
            chat = await pool.fetchval(
                """SELECT telegram_user_id FROM employees
                   WHERE client_id=$1 AND telegram_user_id IS NOT NULL
                     AND (extension=$2 OR lower(full_name)=lower($2) OR lower(full_name)=lower('@' || ltrim($2, '@')))
                   ORDER BY id LIMIT 1""", client_id, token)
            if chat:
                ids.append(chat)
    return list(dict.fromkeys(ids))


async def _delivery_targets(pool, row):
    plan = engine.immediate(row['data'])
    targets = {}
    if plan['manager']:
        chat = await pool.fetchval(
            "SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
            row['client_id'], row['extension'])
        if chat:
            targets['manager'] = [chat]
    copies = await copy_chat_ids(pool, row['client_id'])
    if copies and row['data'].get('reviewed'):
        targets['copy'] = copies
    if plan['rop']:
        targets['rop'] = [chat for chat in await report_chat_ids(pool, row['client_id']) if chat not in copies]
    return targets


async def _seed_deliveries(pool, call_id, targets):
    # Legacy markers also protect historical deliveries and reviews created by backfill.
    async with pool.acquire() as conn, conn.transaction():
        for kind, recipients in targets.items():
            claimed = await conn.fetchval(
                f'UPDATE call_reviews SET {kind}_sent_at=now()'
                f' WHERE call_id=$1 AND {kind}_sent_at IS NULL RETURNING call_id', call_id)
            if claimed and recipients:
                await conn.executemany(
                    'INSERT INTO review_delivery(call_id,kind,recipient) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
                    [(call_id, kind, recipient) for recipient in recipients])


async def _copy_text(pool, row, chat):
    from . import prompts

    try:
        text, problem, cost = await prompts.personal_review(pool, row, chat)
        await prompts.add_spend(pool, row['client_id'], cost)
    except Exception:
        log.exception('личный разбор не собран, call id=%s', row['call_id'])
        text, problem = None, None
    text = text or render.details_message(row['meta'], row['data'])
    if problem:
        notice = render.esc(problem)
        fallback = f'⚠️ Ваш личный промт разбора не сработал: {notice}.\nНиже стандартный разбор.\n\n{text}'
        if len(fallback) <= 4096:
            text = fallback
    return text


async def _send_delivery(pool, bot, row, kind, recipient):
    call_id = row['call_id']
    entry = await pool.fetchrow(
        """UPDATE review_delivery SET status='sending', attempts=attempts+1, updated_at=now()
           WHERE call_id=$1 AND kind=$2 AND recipient=$3 AND status='pending' AND retry_at<=now()
           RETURNING body, attempts""", call_id, kind, recipient)
    if entry is None:
        return False

    keyboard = None
    if kind == 'manager':
        text = render.manager_message(row['meta'], row['data'])
        keyboard = render.keyboard(call_id, train=bool(row['data'].get('key_moment')))
    elif kind == 'rop':
        text = render.rop_message(row['meta'], row['data'])
        keyboard = render.keyboard(call_id)
    else:
        text = entry['body']
        if text is None:
            text = await _copy_text(pool, row, recipient)

    status, error, delay = 'sent', None, timedelta()
    try:
        if keyboard is None:
            await bot.send_message(recipient, text, parse_mode='HTML')
        else:
            await bot.send_message(recipient, text, parse_mode='HTML', reply_markup=keyboard)
    except TelegramRetryAfter as exc:
        error = type(exc).__name__
        if entry['attempts'] < 5:
            status, delay = 'pending', timedelta(seconds=max(1, exc.retry_after))
        else:
            status = 'failed'
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        status, error = 'failed', type(exc).__name__
    except Exception as exc:
        status, error = 'uncertain', type(exc).__name__

    await pool.execute(
        """UPDATE review_delivery SET status=$4, error=$5, retry_at=now()+$6::interval,
                                      body=$7, updated_at=now()
           WHERE call_id=$1 AND kind=$2 AND recipient=$3""", call_id, kind, recipient, status, error, delay,
        text if kind == 'copy' and status == 'pending' else None)
    if error:
        log.warning('доставка разбора call id=%s kind=%s: %s (%s)', call_id, kind, status, error)
    return status == 'sent'


async def deliver_now(pool, bot, call_id: int) -> dict:
    """Retry explicit Telegram throttles through the analysis job; ambiguous sends never replay."""
    from queue_runner import RetryLater

    row = await load(pool, call_id)
    if row is None:
        return {}
    targets = await _delivery_targets(pool, row)
    await _seed_deliveries(pool, call_id, targets)
    sent = {}
    for kind in ('manager', 'copy', 'rop'):
        recipients = targets.get(kind, [])
        await pool.execute(
            """UPDATE review_delivery SET status='failed', error='Recipient removed', body=NULL, updated_at=now()
               WHERE call_id=$1 AND kind=$2 AND status='pending' AND NOT (recipient=ANY($3::bigint[]))""",
            call_id, kind, recipients)
        for recipient in recipients:
            if await _send_delivery(pool, bot, row, kind, recipient):
                if kind == 'manager':
                    sent[kind] = recipient
                else:
                    sent.setdefault(kind, []).append(recipient)

    # A worker crash leaves 'sending' for reconciliation because Telegram has no delivery idempotency key.
    pending = await pool.fetchval(
        """SELECT GREATEST(min(retry_at)-now(), interval '1 second')
           FROM review_delivery WHERE call_id=$1 AND status='pending' HAVING count(*)>0""", call_id)
    if pending is not None:
        raise RetryLater(pending, f'Telegram отложил доставку разбора, call id={call_id}', record_limit=False)
    return sent


async def day_rows(pool, client_id: int, start, end) -> list[dict]:
    rows = await pool.fetch(ROW_SQL + ' WHERE r.client_id=$1 AND coalesce(c.call_started_at, r.created_at) >= $2'
                            ' AND coalesce(c.call_started_at, r.created_at) < $3 ORDER BY coalesce(c.call_started_at, r.created_at)', client_id, start, end)
    return [unpack(r) for r in rows]
