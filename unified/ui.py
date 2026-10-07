from __future__ import annotations

import logging
from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, ReplyParameters
from tools import Forbidden, resolve_actor
from . import store, render, overview_cache
from .extended_review import sections
from comparison.registry import sha
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter


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
        # Compatibility notices must not change the identity/parts of the
        # already cached overview reached through a legacy button.
        await bot.send_message(actor.telegram_user_id, notice, parse_mode=None)
    chunks = sections(text)
    document = render.units(text) > 11500 or chunks is None
    count = 1 if document else len(chunks)
    content_sha = sha(text + ('document' if document else 'sections-v1'))
    sent_here = False
    while True:
        state, index = await overview_cache.claim(pool, actor, run_id, count, content_sha)
        if state != 'claimed':
            if state == 'complete' and not sent_here:
                receipt = await overview_cache.delivered_view(pool, actor, run_id)
                if receipt:
                    message_id = receipt['parts'][0].get('message_id')
                    reply = ReplyParameters(message_id=message_id, allow_sending_without_reply=True) if message_id else None
                    await bot.send_message(actor.telegram_user_id,
                        'Обзор уже готов. Нажмите «Показать обзор здесь», чтобы получить его снова. Новый анализ не потребуется.',
                        parse_mode=None, reply_parameters=reply,
                        reply_markup=render.reopen_keyboard(run_id, receipt.get('generation', 0)))
            elif state in {'sending', 'uncertain'}:
                await bot.send_message(actor.telegram_user_id, 'Отправка обзора ещё выполняется или её результат неизвестен. Уже доставленные части повторно не отправляются.', parse_mode=None)
            return
        try:
            if document:
                message = await bot.send_document(actor.telegram_user_id, BufferedInputFile(text.encode('utf-8'), filename=f'Обзор_{run_id}.txt'),
                                    caption='Расширенный разбор', parse_mode=None, reply_markup=render.back_keyboard(run_id))
            else:
                prefix = f'Часть {index + 1}/{count}\n\n' if count > 1 else ''
                message = await bot.send_message(actor.telegram_user_id, prefix + chunks[index], parse_mode=None,
                                    reply_markup=render.back_keyboard(run_id) if index == count - 1 else None)
        except (TelegramBadRequest, TelegramRetryAfter):
            await overview_cache.record(pool, actor, run_id, index, 'pending')
            raise
        except TelegramForbiddenError:
            await overview_cache.record(pool, actor, run_id, index, 'forbidden')
            raise
        except Exception:
            await overview_cache.record(pool, actor, run_id, index, 'uncertain')
            raise
        await overview_cache.record(pool, actor, run_id, index, 'sent', message.message_id)
        sent_here = True


def install(router, pool_getter):
    from .analysis_policy import install as install_policy
    install_policy(router, pool_getter)
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
            reopening = len(bits) == 4 and bits[1] == 'r'
            if (not (len(bits) == 3 and bits[1] in {'e', 's', 'x'} or reopening)
                    or not bits[2].isascii() or not bits[2].isdigit() or len(bits[2]) > 18
                    or reopening and (not bits[3].isascii() or not bits[3].isdigit() or len(bits[3]) > 18)):
                raise ValueError('Кнопка устарела. Откройте сохранённый разбор через /review и номер звонка.')
            pool = pool_getter(); actor = await resolve_actor(pool, cq.from_user.id)
            run_id = int(bits[2])
            await store.access(pool, actor, run_id)
            if bits[1] == 'x':
                await overview_cache.close(pool, cq.bot, actor, run_id, cq.message.message_id)
            elif reopening:
                await overview_cache.reopen(pool, actor, run_id, int(bits[3]))
                await show(cq.bot, pool, actor, run_id, detailed=True)
            else:
                await show(cq.bot, pool, actor, run_id, detailed=bits[1] == 'e')
        except (Forbidden, ValueError) as exc:
            await cq.bot.send_message(cq.from_user.id, str(exc), parse_mode=None)
        except Exception:
            logging.getLogger(__name__).exception('run-bound overview failed')
            await cq.bot.send_message(cq.from_user.id, 'Не удалось открыть обзор. Обычный разбор сохранён; нажмите «Расширенный обзор» позже.', parse_mode=None)
