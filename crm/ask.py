"""Вопрос по отделу: тот же агент РОПа, что отвечает на свободный текст в боте.

Права решают инструменты агента по Actor: менеджер получает данные только
о своих звонках. Вопросы и ответы храним, чтобы история была на сайте.
"""
from __future__ import annotations

import logging

from tools import is_privileged
from . import journal

log = logging.getLogger(__name__)
MAX_QUESTION = 2000


def owner_of(actor) -> tuple[str, str | None]:
    return ('director', None) if is_privileged(actor) else ('manager', actor.extension)


async def history(pool, actor, limit: int = 30) -> list[dict]:
    role, ext = owner_of(actor)
    rows = await pool.fetch(
        """SELECT * FROM crm_questions WHERE client_id=$1 AND role=$2 AND extension IS NOT DISTINCT FROM $3
           ORDER BY created_at DESC LIMIT $4""", actor.client_id, role, ext, limit)
    return [{'id': r['id'], 'question': r['question'], 'html': journal.html(r['answer']) if r['answer'] else None,
             'at': r['created_at'].isoformat()} for r in rows]


async def ask(pool, actor, question: str, answer=None, save: bool = True):
    """save=False: только текст ответа, без записи (так спрашивает AveronGPT, у него своя история)."""
    question = str(question or '').strip()
    if not question:
        raise ValueError('Напишите вопрос.')
    if len(question) > MAX_QUESTION:
        raise ValueError('Слишком длинный вопрос.')
    if answer is None:
        import rop_agent  # тяжёлый импорт (клиент модели) только когда спрашивают
        answer = rop_agent.answer
    if not save:
        return await answer(pool, actor, question, allow_course=True)
    role, ext = owner_of(actor)
    qid = await pool.fetchval('INSERT INTO crm_questions(client_id,role,extension,question) VALUES($1,$2,$3,$4) RETURNING id',
                              actor.client_id, role, ext, question)
    try:
        text = await answer(pool, actor, question, allow_course=True)
    except Exception:
        log.exception('агент РОПа не ответил на вопрос из CRM')
        text = 'Не получилось обработать вопрос, попробуйте ещё раз.'
    await pool.execute('UPDATE crm_questions SET answer=$2 WHERE id=$1', qid, text)
    return {'id': qid, 'question': question, 'html': journal.html(text)}
