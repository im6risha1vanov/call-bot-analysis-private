"""Команда /crm в боте: одноразовая ссылка для входа в CRM."""
from __future__ import annotations

import logging
import os

from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tools import resolve_actor
from . import auth

log = logging.getLogger(__name__)


def base_url() -> str | None:
    url = (os.getenv('CRM_URL') or '').strip().rstrip('/')
    return url if url.startswith('https://') else None


def install(router, pool_getter):
    @router.message(Command('crm'))
    async def crm_command(message):
        if message.chat.type != 'private':
            await message.answer('Ссылку на CRM пришлю только в личном чате.', parse_mode=None)
            return
        url = base_url()
        if url is None:
            await message.answer('CRM ещё не подключена.', parse_mode=None)
            return
        pool = pool_getter()
        actor = await resolve_actor(pool, message.from_user.id)
        if actor is None:
            await message.answer('Вы не подключены к боту. Попросите руководителя прислать приглашение.', parse_mode=None)
            return
        try:
            token = await auth.issue_login(pool, message.from_user.id)
        except Exception:
            log.exception('не удалось выдать ссылку в CRM')
            await message.answer('Не получилось выдать ссылку. Попробуйте позже.', parse_mode=None)
            return
        minutes = int(auth.LOGIN_TTL.total_seconds() // 60)
        await message.answer(
            f'Ссылка для входа в CRM. Она одноразовая и действует {minutes} минут, после входа CRM помнит вас 30 дней. '
            'Никому её не пересылайте.',
            parse_mode=None, disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Открыть CRM', url=f'{url}/in/{token}')]]))
