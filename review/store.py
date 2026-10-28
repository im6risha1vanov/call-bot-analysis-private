"""Хранение разбора (call_reviews) и доставка: сразу и в вечерней сводке."""
from __future__ import annotations

import json
import logging
import os
import re
from zoneinfo import ZoneInfo

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


async def deliver_now(pool, bot, call_id: int) -> dict:
    """Сразу: менеджеру — потерянный шанс, РОПу — только горячий,
    получателям из REVIEW_COPY_TO — полный текст каждого разбора с разговором.

    Отметка ставится до отправки: при сбое между отправкой и записью лучше
    недоставить, чем прислать дубль (то же правило, что в unified/store)."""
    row = await load(pool, call_id)
    if row is None:
        return {}
    plan = engine.immediate(row['data'])
    sent = {}
    if plan['manager']:
        chat = await pool.fetchval(
            "SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
            row['client_id'], row['extension'])
        claimed = chat and await pool.fetchval(
            'UPDATE call_reviews SET manager_sent_at=now() WHERE call_id=$1 AND manager_sent_at IS NULL RETURNING call_id', call_id)
        if claimed:
            try:
                await bot.send_message(chat, render.manager_message(row['meta'], row['data']), parse_mode='HTML',
                                       reply_markup=render.keyboard(call_id, train=bool(row['data'].get('key_moment'))))
                sent['manager'] = chat
            except Exception:
                log.exception('разбор менеджеру не доставлен, call id=%s', call_id)
    copies = await copy_chat_ids(pool, row['client_id'])
    if copies and row['data'].get('reviewed'):
        claimed = await pool.fetchval(
            'UPDATE call_reviews SET copy_sent_at=now() WHERE call_id=$1 AND copy_sent_at IS NULL RETURNING call_id', call_id)
        if claimed:
            from . import prompts
            for chat in copies:
                # Личный промт из режима промта: свой разбор этого звонка; если сломался —
                # причина и стандартная копия.
                try:
                    text, problem, cost = await prompts.personal_review(pool, row, chat)
                    await prompts.add_spend(pool, row['client_id'], cost)
                except Exception:
                    log.exception('личный разбор не собран, call id=%s chat=%s', call_id, chat)
                    text, problem = None, None
                try:
                    if problem:
                        await bot.send_message(chat, f'⚠️ Ваш личный промт разбора не сработал на этом звонке: {problem}. '
                                                     'Ниже стандартный разбор.', parse_mode=None)
                    await bot.send_message(chat, text or render.details_message(row['meta'], row['data']), parse_mode='HTML')
                    sent.setdefault('copy', []).append(chat)
                except Exception:
                    log.exception('копия разбора не доставлена, call id=%s chat=%s', call_id, chat)
    if plan['rop']:
        claimed = await pool.fetchval(
            'UPDATE call_reviews SET rop_sent_at=now() WHERE call_id=$1 AND rop_sent_at IS NULL RETURNING call_id', call_id)
        if claimed:
            for chat in await report_chat_ids(pool, row['client_id']):
                if chat in copies:
                    continue  # уже получил полный разбор
                try:
                    await bot.send_message(chat, render.rop_message(row['meta'], row['data']), parse_mode='HTML',
                                           reply_markup=render.keyboard(call_id))
                    sent.setdefault('rop', []).append(chat)
                except Exception:
                    log.exception('разбор РОПу не доставлен, call id=%s chat=%s', call_id, chat)
    return sent


async def day_rows(pool, client_id: int, start, end) -> list[dict]:
    rows = await pool.fetch(ROW_SQL + ' WHERE r.client_id=$1 AND coalesce(c.call_started_at, r.created_at) >= $2'
                            ' AND coalesce(c.call_started_at, r.created_at) < $3 ORDER BY coalesce(c.call_started_at, r.created_at)', client_id, start, end)
    return [unpack(r) for r in rows]
