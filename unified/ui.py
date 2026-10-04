from __future__ import annotations

import logging
from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from methodology.messages import split_plain
from tools import Forbidden, resolve_actor
from . import store, render


def private_action(event):
    user = event.from_user
    message = event.message if hasattr(event, 'message') else event
    if message is None or message.chat.type != 'private' or message.chat.id != user.id:
        raise Forbidden('Откройте свои разборы в личном чате с ботом.')


async def show(bot, pool, actor, run_id, *, detailed=False, notice=None):
    row = await store.access(pool, actor, run_id)
    if row['status'] != 'ready':
        row = await store.prepare(pool, run_id, wait=False)
    if not detailed:
        await bot.send_message(actor.telegram_user_id, row['summary'], parse_mode='HTML', reply_markup=render.keyboard(run_id))
        return
    text = await store.overview(pool, actor, run_id)
    if notice:
        text = notice + '\n\n' + text
    # One document handles arbitrarily long criteria sets without flooding the chat.
    if render.units(text) > 11500:
        await bot.send_document(actor.telegram_user_id, BufferedInputFile(text.encode('utf-8'), filename=f'Обзор_звонка_{row["call_id"]}_{run_id}.txt'),
                                caption=f'Расширенный обзор звонка №{row["call_id"]}.', parse_mode=None,
                                reply_markup=render.back_keyboard(run_id))
    else:
        chunks = list(split_plain(text, limit=3700))
        for i, chunk in enumerate(chunks, 1):
            prefix = f'Расширенный обзор · часть {i}/{len(chunks)}\n\n' if len(chunks) > 1 else ''
            await bot.send_message(actor.telegram_user_id, prefix + chunk, parse_mode=None,
                                   reply_markup=render.back_keyboard(run_id) if i == len(chunks) else None)


def install(router, pool_getter):
    @router.message(Command('review'))
    async def command(message):
        try:
            private_action(message)
            pool = pool_getter(); actor = await resolve_actor(pool, message.from_user.id)
            parts = (message.text or '').split()
            if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 18:
                raise ValueError('Укажите номер звонка: /review 123. Это открывает сохранённый разбор.')
            run_id = await store.saved_view(pool, actor, int(parts[1]))
            await show(message.bot, pool, actor, run_id)
        except (Forbidden, ValueError) as exc:
            await message.answer(str(exc), parse_mode=None)
        except Exception:
            logging.getLogger(__name__).exception('saved report command failed')
            await message.answer('Не удалось открыть разбор. Попробуйте позже.', parse_mode=None)

    @router.callback_query(F.data.startswith('uv:'))
    async def callback(cq):
        await cq.answer()  # Always first, including failures and denied requests.
        try:
            private_action(cq)
            bits = (cq.data or '').split(':')
            if len(bits) != 3 or bits[1] not in {'e', 's', 'x'} or not bits[2].isascii() or not bits[2].isdigit() or len(bits[2]) > 18:
                raise ValueError('Кнопка устарела. Откройте сохранённый разбор через /review и номер звонка.')
            pool = pool_getter(); actor = await resolve_actor(pool, cq.from_user.id)
            run_id = int(bits[2])
            await store.access(pool, actor, run_id)
            if bits[1] == 'x':
                # Close only the clicked overview. Never delete the ordinary result.
                await cq.message.delete()
            else:
                await show(cq.bot, pool, actor, run_id, detailed=bits[1] == 'e')
        except (Forbidden, ValueError) as exc:
            await cq.bot.send_message(cq.from_user.id, str(exc), parse_mode=None)
        except Exception:
            logging.getLogger(__name__).exception('run-bound overview failed')
            await cq.bot.send_message(cq.from_user.id, 'Не удалось открыть обзор. Обычный разбор сохранён; нажмите «Расширенный обзор» позже.', parse_mode=None)
