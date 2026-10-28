"""Журнал отчётов бота для CRM: дайджесты РОПа и итоги дня менеджерам.

Бот по-прежнему шлёт их в Telegram, а копию кладёт сюда (save). Сбой записи
не мешает рассылке. Текст хранится в разметке Telegram (<b>, <i>, <code>);
на сайт он уходит через html(), где остаются только эти теги.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re

log = logging.getLogger(__name__)

KINDS = {
    'morning': 'На планёрку',
    'evening': 'Вечерний отчёт по отделу',
    'weekly': 'Сводка за неделю',
    'monthly': 'Отчёт за месяц',
    'day_rop': 'Итог дня по отделу',
    'day_manager': 'Итог дня менеджера',
}
MANAGER_KINDS = {'day_manager'}
ALLOWED = re.compile(r'&lt;(/?)(b|i|u|s|code|pre)&gt;')


async def save(pool, client_id: int, kind: str, body: str, *, extension: str | None = None,
               calls: list[tuple[int, str]] | None = None, note: str | None = None) -> None:
    try:
        await pool.execute(
            'INSERT INTO crm_reports(client_id,kind,title,extension,body,calls,note) VALUES($1,$2,$3,$4,$5,$6::jsonb,$7)',
            client_id, kind, KINDS.get(kind, kind), extension, body,
            json.dumps([{'id': int(i), 'label': str(label)} for i, label in calls or []], ensure_ascii=False), note)
    except Exception:
        log.exception('отчёт %s не сохранился в CRM', kind)


def html(body: str) -> str:
    """Разметка Telegram в безопасный HTML: всё экранируем и возвращаем только простые теги."""
    text = _html.escape(_html.unescape(re.sub(r'</?a\b[^>]*>', '', body or '')), quote=False)
    return ALLOWED.sub(lambda m: f'<{m[1]}{m[2]}>', text)


async def listing(pool, actor, limit: int = 120) -> list[dict]:
    """Менеджер видит только свои итоги дня, руководитель всё."""
    from tools import is_privileged
    if is_privileged(actor):
        rows = await pool.fetch('SELECT * FROM crm_reports WHERE client_id=$1 ORDER BY created_at DESC LIMIT $2',
                                actor.client_id, limit)
    else:
        rows = await pool.fetch(
            """SELECT * FROM crm_reports WHERE client_id=$1 AND kind = ANY($2::text[]) AND extension=$3
               ORDER BY created_at DESC LIMIT $4""", actor.client_id, sorted(MANAGER_KINDS), actor.extension, limit)
    names = {r['extension']: r['full_name'] for r in await pool.fetch(
        "SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager'", actor.client_id)}
    return [{'id': r['id'], 'kind': r['kind'], 'title': r['title'], 'at': r['created_at'].isoformat(),
             'who': names.get(r['extension']) if r['extension'] else None, 'note': r['note'],
             'html': html(r['body']), 'calls': json.loads(r['calls']) if isinstance(r['calls'], str) else r['calls']}
            for r in rows]
