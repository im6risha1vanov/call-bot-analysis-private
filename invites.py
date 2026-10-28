"""Ссылка-приглашение в бота: t.me/<бот>?start=inv_<code>.

Числовой ID Telegram бот узнаёт, только когда человек сам ему напишет, а ник есть
не у всех. Поэтому сотрудника заводят заранее (scripts/invite.sh), а ссылка
одноразово привязывает к этой строке того, кто её открыл. Имя берётся из
Telegram: в командах сервера только латиница, кириллица в них портится.
"""
from __future__ import annotations

import re

PREFIX = 'inv_'
CODE = re.compile(r'inv_([A-Za-z0-9]{8,40})')


def code_from(text: str | None) -> str | None:
    """Код из «/start inv_xxx» или None."""
    parts = (text or '').split(maxsplit=1)
    m = CODE.fullmatch(parts[1].strip()) if len(parts) == 2 else None
    return m[1] if m else None


async def accept(pool, code: str, tg_id: int, name: str | None) -> tuple[dict | None, str | None]:
    """(строка сотрудника, None) — подключён; (None, причина) — нет."""
    async with pool.acquire() as conn, conn.transaction():
        invite = await conn.fetchrow('SELECT *, expires_at < now() AS expired FROM employee_invites WHERE code=$1 FOR UPDATE', code)
        if invite is None or invite['used_at'] is not None:
            return None, 'Ссылка-приглашение уже использована или неверна. Попросите новую.'
        if invite['expired']:
            return None, 'Срок ссылки-приглашения истёк. Попросите новую.'
        if await conn.fetchval('SELECT 1 FROM employees WHERE telegram_user_id=$1', tg_id):
            return None, 'Вы уже подключены к боту. Если нужна другая роль, напишите руководителю.'
        row = await conn.fetchrow(
            """UPDATE employees SET telegram_user_id=$3, full_name=coalesce(nullif($4, ''), full_name)
               WHERE client_id=$1 AND extension=$2 AND telegram_user_id IS NULL
               RETURNING client_id, extension, role, full_name""",
            invite['client_id'], invite['extension'], tg_id, (name or '').strip()[:100])
        if row is None:
            return None, 'Это место в боте уже занято. Попросите новую ссылку.'
        await conn.execute('UPDATE employee_invites SET used_at=now(), used_by=$2 WHERE code=$1', code, tg_id)
        return dict(row), None
