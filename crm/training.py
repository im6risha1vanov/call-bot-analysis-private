"""Тренировки в CRM: назначить менеджеру, «Потренировать» ошибку из разбора,
посмотреть, как прошли тренировки.

Сам тренажёр остаётся ботом в Telegram (там голос): CRM создаёт назначение и
даёт ссылку t.me/<бот>?start=train_<id>, по которой тренировка стартует.
Права как в боте: менеджер видит только свои тренировки, назначает руководитель.
"""
from __future__ import annotations

import json
import logging
import os

import httpx

from tools import Forbidden, is_privileged

log = logging.getLogger(__name__)
MODES = {'dialog': 'разговор целиком', 'drill': 'отработка возражений'}
STATUS = {'active': 'идёт', 'abandoned': 'брошена', 'failed': 'сбой'}
LEVELS = {'✅': '✅ справился', '⚠️': '⚠️ частично', '❌': '❌ не справился'}


def bot() -> str:
    return os.getenv('TRAIN_BOT_USERNAME', '').strip().lstrip('@')


def link(assignment_id: int) -> str | None:
    return f'https://t.me/{bot()}?start=train_{assignment_id}' if bot() else None


def loads(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def result(row) -> str:
    if row['status'] != 'completed':
        return STATUS.get(row['status'], row['status'])
    if row['mode'] == 'drill':
        state = loads(row['drill_state'], {})
        results = state.get('results') or []
        if not results:
            return 'разбор по курсу' if state.get('version') else 'без оценки'
        return f"зачтено {sum(1 for r in results if r.get('passed'))} из {len(results)}"
    return LEVELS.get(row['level'], row['level']) if row['level'] else 'без оценки'


async def listing(pool, actor, ext: str | None) -> dict:
    if not is_privileged(actor):
        ext = actor.extension
    rows = await pool.fetch(
        """SELECT t.id, t.extension, t.mode, t.status, t.level, t.drill_state, t.topic, t.started_at, t.is_test, e.full_name
           FROM training_sessions t
           LEFT JOIN employees e ON e.client_id=t.client_id AND e.extension=t.extension AND e.role='manager'
           WHERE t.client_id=$1 AND ($2::text IS NULL OR t.extension=$2)
           ORDER BY t.started_at DESC LIMIT 60""", actor.client_id, ext)
    pending = await pool.fetch(
        """SELECT p.id, p.extension, p.mode, p.topic, e.full_name FROM pending_train_assignments p
           LEFT JOIN employees e ON e.client_id=p.client_id AND e.extension=p.extension AND e.role='manager'
           WHERE p.client_id=$1 AND NOT p.consumed AND ($2::text IS NULL OR p.extension=$2)
           ORDER BY p.id DESC LIMIT 30""", actor.client_id, ext)
    return {
        'bot': bool(bot()),
        'items': [{'id': r['id'], 'who': r['full_name'] or f"доб. {r['extension']}", 'ext': r['extension'],
                   'mode': MODES.get(r['mode'], r['mode']), 'topic': topic_text(r['topic']), 'result': result(r),
                   'at': r['started_at'].isoformat() if r['started_at'] else None, 'test': r['is_test']} for r in rows],
        'pending': [{'id': p['id'], 'who': p['full_name'] or f"доб. {p['extension']}", 'mode': MODES.get(p['mode'], p['mode']),
                     'topic': topic_text(p['topic']),
                     # Ссылку на старт даём только самому менеджеру: тренажёр всё равно пустит лишь его.
                     'link': link(p['id']) if not is_privileged(actor) else None} for p in pending],
    }


def topic_text(topic) -> str | None:
    topic = str(topic or '')
    if topic.startswith('review:'):
        return f'ошибка из звонка {topic.removeprefix("review:")}'
    return topic or None


async def detail(pool, actor, training_id: int) -> dict:
    row = await pool.fetchrow(
        """SELECT t.*, e.full_name FROM training_sessions t
           LEFT JOIN employees e ON e.client_id=t.client_id AND e.extension=t.extension AND e.role='manager'
           WHERE t.id=$1""", training_id)
    if row is None or row['client_id'] != actor.client_id:
        raise Forbidden('Тренировка не найдена.')
    if not is_privileged(actor) and row['extension'] != actor.extension:
        raise Forbidden('Это тренировка другого сотрудника.')
    turns, checks = [], []
    state = loads(row['drill_state'], {})
    if row['mode'] == 'drill' and state.get('results'):
        checks = [{'objection': r.get('objection'), 'answer': str(r.get('answer') or '')[:1500],
                   'passed': bool(r.get('passed')), 'comment': r.get('comment')} for r in state['results']]
    else:
        turns = [{'who': 'Менеджер' if t.get('role') == 'manager' else 'Клиент', 'text': str(t.get('text') or '')[:1500]}
                 for t in loads(row['transcript'], [])]
    return {'id': row['id'], 'who': row['full_name'] or f"доб. {row['extension']}", 'mode': MODES.get(row['mode'], row['mode']),
            'topic': topic_text(row['topic']), 'result': result(row), 'test': row['is_test'],
            'at': row['started_at'].isoformat() if row['started_at'] else None, 'turns': turns, 'checks': checks}


async def assign(pool, actor, ext: str, mode: str, topic: str | None) -> dict:
    if not is_privileged(actor):
        raise Forbidden('Назначает тренировки руководитель.')
    if mode not in MODES:
        raise ValueError('Выберите режим.')
    if not await pool.fetchval("SELECT 1 FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                               actor.client_id, str(ext or '')):
        raise ValueError('Такого менеджера нет.')
    topic = (str(topic or '').strip() or None)
    if topic and len(topic) > 200:
        raise ValueError('Слишком длинная тема.')
    assignment_id = await pool.fetchval(
        """INSERT INTO pending_train_assignments(client_id, extension, topic, mode, assigned_by_telegram_user_id)
           VALUES($1,$2,$3,$4,$5) RETURNING id""", actor.client_id, str(ext), topic, mode, actor.telegram_user_id or None)
    await notify(pool, actor.client_id, str(ext), assignment_id, mode, topic)
    return {'id': assignment_id}


async def notify(pool, client_id: int, ext: str, assignment_id: int, mode: str, topic: str | None) -> bool:
    """Сообщение менеджеру в основном боте с кнопкой старта, как у /assign_train. Не вышло — назначение всё равно видно в CRM."""
    chat = await pool.fetchval("SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                               client_id, ext)
    token, url = os.getenv('BOT_TOKEN'), link(assignment_id)
    if not chat or not token or not url:
        return False
    text = f'Руководитель назначил тренировку: {MODES[mode]}' + (f', тема «{topic}»' if topic else '') + '. Она пройдёт в боте-тренажёре.'
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f'https://api.telegram.org/bot{token}/sendMessage', json={
                'chat_id': chat, 'text': text,
                'reply_markup': {'inline_keyboard': [[{'text': 'Начать тренировку', 'url': url}]]}})
        return r.status_code == 200
    except Exception:
        log.warning('не удалось написать менеджеру доб. %s о тренировке', ext)
        return False
