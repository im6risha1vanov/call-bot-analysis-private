"""Кнопки под разбором («Подробнее») и под перезвонами (статус)."""
from __future__ import annotations

import logging

from aiogram import F

from reports import send_long
from tools import Forbidden, resolve_actor
from unified.ui import private_action
from . import callbacks, render, store


def install(router, pool_getter):
    @router.callback_query(F.data.startswith('rv:'))
    async def callback(cq):
        await cq.answer()
        try:
            private_action(cq)
            bits = (cq.data or '').split(':')
            if len(bits) != 3 or bits[1] not in {'d', 't'} or not bits[2].isascii() or not bits[2].isdigit() or len(bits[2]) > 18:
                raise ValueError('Кнопка устарела.')
            pool = pool_getter()
            actor = await resolve_actor(pool, cq.from_user.id)
            if bits[1] == 't':
                raise Forbidden('Действие недоступно.')
            row = await store.access(pool, actor, int(bits[2]))
            await send_long(cq.bot, cq.from_user.id, render.details_message(row['meta'], row['data']))
        except (Forbidden, ValueError) as exc:
            await cq.bot.send_message(cq.from_user.id, str(exc), parse_mode=None)
        except Exception:
            logging.getLogger(__name__).exception('review details failed')
            await cq.bot.send_message(cq.from_user.id, 'Не удалось открыть разбор. Попробуйте позже.', parse_mode=None)

    @router.callback_query(F.data.regexp(r'^cb:[a-z]:\d+$'))
    async def callback_task(cq):
        await cq.answer()
        try:
            private_action(cq)
            bits = (cq.data or '').split(':')
            if len(bits) != 3 or bits[1] not in callbacks.ACTIONS or not bits[2].isascii() or not bits[2].isdigit() or len(bits[2]) > 18:
                raise ValueError('Кнопка устарела.')
            pool = pool_getter()
            actor = await resolve_actor(pool, cq.from_user.id)
            note = await callbacks.change(pool, actor, int(bits[2]), callbacks.ACTIONS[bits[1]])
            await cq.bot.send_message(cq.from_user.id, note, parse_mode=None)
        except (Forbidden, ValueError) as exc:
            await cq.bot.send_message(cq.from_user.id, str(exc), parse_mode=None)
        except Exception:
            logging.getLogger(__name__).exception('callback task action failed')
            await cq.bot.send_message(cq.from_user.id, 'Не удалось изменить перезвон. Попробуйте позже.', parse_mode=None)
