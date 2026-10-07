"""Кнопки под разбором («Подробнее») и под перезвонами (статус)."""
from __future__ import annotations

import logging

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
                if not render.train_bot():
                    raise ValueError('Тренажёр не подключён.')
                assignment_id = await store.train_assignment(pool, actor, int(bits[2]))
                url = f'https://t.me/{render.train_bot()}?start=train_{assignment_id}'
                await cq.bot.send_message(
                    cq.from_user.id, 'Тренажёр повторит этот момент звонка: клиент тот же, попробуй довести до шага.',
                    parse_mode=None, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text='Начать тренировку', url=url)]]))
                return
            row = await store.access(pool, actor, int(bits[2]))
            await send_long(cq.bot, cq.from_user.id, render.details_message(row['meta'], row['data']))
        except (Forbidden, ValueError) as exc:
            await cq.bot.send_message(cq.from_user.id, str(exc), parse_mode=None)
        except Exception:
            logging.getLogger(__name__).exception('review details failed')
            await cq.bot.send_message(cq.from_user.id, 'Не удалось открыть разбор. Попробуйте позже.', parse_mode=None)

    @router.callback_query(F.data.startswith('cb:'))
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
