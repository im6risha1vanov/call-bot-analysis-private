"""Кнопка «✏️ Режим промта»: правка своих промтов в диалоге с моделью.

Выбрал, что править → пишешь обычным текстом, что поменять → модель правит текст,
бот показывает, какие строки изменились → «Пример» прогоняет черновик на последнем
звонке (или собирает отчёт) → «Сохранить для себя» или «для всех». Разбор звонка для всех
сохраняется только после «🧪 Проверки на звонках» (review/prompt_check.py): тот же
текст на 20 последних звонках против действующего, где поменялся итог. «Сохранения» —
список, к любому можно вернуться, как к сохранению в игре; исходный текст в нём
всегда есть. Хранение и применение — review/prompts.py.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
from zoneinfo import ZoneInfo

from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard

from reports import send_long
from tools import Forbidden, resolve_actor
from unified.ui import private_action
from . import prompt_check, prompts

log = logging.getLogger(__name__)

MENU = '✏️ Режим промта'
MSK = ZoneInfo('Europe/Moscow')
SOURCE = {'personal': 'ваша личная версия', 'common': 'общая версия', 'default': 'исходная версия'}


def kb(*rows) -> Keyboard:
    return Keyboard(inline_keyboard=[[Button(text=t, callback_data=d) for t, d in row] for row in rows if row])


def kinds_keyboard() -> Keyboard:
    return kb(*[[(prompts.TITLES[k], f'pm:k:{k}')] for k in prompts.KINDS], [('🚪 Выйти', 'pm:exit')])


def session_keyboard(changed: bool) -> Keyboard:
    return kb([('👁 Пример', 'pm:prev'), ('📄 Весь текст', 'pm:cur')] if changed else [('📄 Весь текст', 'pm:cur')],
              [('↩️ Отменить правки', 'pm:undo')] if changed else None,
              [('🗂 Сохранения', 'pm:ver'), ('📋 Другой промт', 'pm:menu'), ('🚪 Выйти', 'pm:exit')])


def save_keyboard(kind: str = '') -> Keyboard:
    check = [(f'🧪 Проверить на {prompt_check.CALLS} звонках', 'pm:check')] if kind == 'review' else None
    return kb([('💾 Сохранить для себя', 'pm:save:me')], check, [('💾 Сохранить для всех', 'pm:save:all')],
              [('✏️ Править дальше', 'pm:more'), ('🚪 Выйти', 'pm:exit')])


# Какой текст разбора человек проверил на звонках: tg -> подпись текста. После перезапуска
# бота проверку просто прогоняют ещё раз.
_checked: dict[int, str] = {}


def body_sign(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def checked(tg: int, body: str) -> bool:
    return _checked.get(tg) == body_sign(body)


async def names(pool, client_id: int) -> dict[int, str]:
    rows = await pool.fetch('SELECT telegram_user_id, full_name FROM employees WHERE client_id=$1 AND telegram_user_id IS NOT NULL',
                            client_id)
    return {r['telegram_user_id']: r['full_name'] or str(r['telegram_user_id']) for r in rows}


async def editor_actor(pool, tg_id: int):
    if not prompts.is_editor(tg_id):
        raise Forbidden('Режим промта вам не подключён.')
    actor = await resolve_actor(pool, tg_id)
    if actor is None:
        raise Forbidden('Режим промта вам не подключён.')
    return actor


def scope_word(owner_tg: int) -> str:
    return 'для всех' if owner_tg == 0 else 'личная'


async def status_text(pool, tg_id: int, kind: str) -> str:
    _, source, version = await prompts.resolve(pool, kind, tg_id)
    where = SOURCE[source] + (f' (сохранение #{version})' if version else '')
    hint = ('Пишите обычным текстом, что поменять: например, «убери раздел про критерии» или '
            '«пиши короче и добавь, сколько звонков у каждого менеджера». Я поправлю текст и покажу, что изменилось. '
            'Сохранить можно после примера.')
    extra = ''
    if kind == 'review':
        extra = ('\n\nЛичная версия меняет только полную копию разбора, которая приходит вам (один дополнительный '
                 'запрос к модели на звонок). Версия «для всех» меняет разборы всех звонков, в том числе менеджерам. '
                 'Формат ответа модели правится только в коде.')
    return f'<b>{MENU}: {prompts.TITLES[kind]}</b>\nСейчас у вас действует {where}.\n\n{hint}{extra}'


async def send_text(bot, chat: int, title: str, body: str) -> None:
    data = body.encode('utf-8')
    await bot.send_document(chat, BufferedInputFile(data, filename=f'{title}.txt'), caption=title[:1000])


async def preview(pool, bot, actor, kind: str, body: str) -> tuple[str | None, str | None, int | None]:
    """Пример по черновику: (текст для показа или None, причина сбоя или None, call_id примера)."""
    if kind == 'review':
        from . import render, store
        call_id = await pool.fetchval(
            """SELECT r.call_id FROM call_reviews r JOIN calls c ON c.id=r.call_id
               WHERE r.client_id=$1 AND (r.data->>'reviewed')='true' AND coalesce(c.transcript,'')<>''
               ORDER BY c.call_started_at DESC NULLS LAST LIMIT 1""", actor.client_id)
        if call_id is None:
            return None, 'нет ни одного разобранного звонка с расшифровкой', None
        row = await store.load(pool, call_id)
        transcript = await pool.fetchval('SELECT transcript FROM calls WHERE id=$1', call_id)
        try:
            data, cost = await prompts.engine.run(transcript, await prompts.review_meta(pool, row),
                                                  prompt=prompts.full_review(body))
        except Exception as exc:
            log.exception('пример разбора не собран')
            return None, prompts.short_error(exc), call_id
        await prompts.add_spend(pool, actor.client_id, cost)
        reason = prompts.broken_review(data, row['data'])
        if reason:
            return None, reason, call_id
        return render.details_message(row['meta'], data), None, call_id
    import rop_agent
    tz = await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', actor.client_id) or 'Europe/Moscow'
    question = prompts.render_digest(body, prompts.digest_values(kind, tz))
    try:
        # Как в рассылке (handlers/rop_digest._broadcast): ответ агента уже в HTML.
        return await rop_agent.answer(pool, actor, question), None, None
    except Exception as exc:
        log.exception('пример отчёта не собран')
        return None, prompts.short_error(exc), None


def install(router, pool_getter):
    async def open_menu(message):
        pool = pool_getter()
        if pool is None:
            return
        try:
            private_action(message)
            await editor_actor(pool, message.from_user.id)
        except Forbidden as exc:
            await message.answer(str(exc), parse_mode=None)
            return
        await prompts.seed(pool)
        await message.answer('Какой промт правим?', parse_mode=None, reply_markup=kinds_keyboard())

    router.message(Command('prompt'))(open_menu)
    router.message(F.text == MENU)(open_menu)

    async def in_session(message) -> bool:
        pool = pool_getter()
        text = message.text or ''
        if pool is None or text.startswith('/') or not prompts.is_editor(message.from_user.id):
            return False
        if message.chat.type != 'private':
            return False
        return await prompts.draft(pool, message.from_user.id) is not None

    @router.message(F.text, in_session)
    async def ask(message):
        pool = pool_getter()
        tg = message.from_user.id
        draft = await prompts.draft(pool, tg)
        if draft is None:
            return
        status = await message.answer('Правлю текст…', parse_mode=None)
        history = list(json.loads(draft['history']) if isinstance(draft['history'], str) else draft['history'] or [])
        try:
            actor = await editor_actor(pool, tg)
            result, cost = await prompts.edit(draft['kind'], draft['body'], history, message.text)
            await prompts.add_spend(pool, actor.client_id, cost)
        except Exception as exc:
            log.exception('правка промта не удалась')
            await status.edit_text(f'Не получилось поправить: {prompts.short_error(exc)}. Попробуйте ещё раз.', parse_mode=None)
            return
        history.append({'ask': message.text[:500], 'reply': result['reply'][:500]})
        fields = {'history': history[-12:]}
        new = result['prompt']
        if new:
            fields.update(body=new, note=result['changes'] or message.text[:200], preview_ok=None, previewed_body=None)
        await prompts.update_draft(pool, tg, **fields)
        body = new or draft['body']
        lines = [html.escape(result['reply'])]
        if new:
            lines += ['', '<b>Что изменилось:</b>', prompts.diff(draft['body'], new) or 'Только пробелы и переносы.']
        elif draft['body'] == draft['start_body']:
            lines += ['', 'Текст не менялся.']
        await status.delete()
        await send_long(message.bot, message.chat.id, '\n'.join(lines))
        await message.answer('Дальше: напишите ещё правку или посмотрите пример.' if body != draft['start_body']
                             else 'Напишите, что поменять.', parse_mode=None,
                             reply_markup=session_keyboard(body != draft['start_body']))

    @router.callback_query(F.data.startswith('pm:'))
    async def callback(cq):
        await cq.answer()
        pool = pool_getter()
        if pool is None:
            return
        tg = cq.from_user.id
        bot = cq.bot
        try:
            private_action(cq)
            actor = await editor_actor(pool, tg)
            bits = (cq.data or '').split(':')
            action = bits[1] if len(bits) > 1 else ''
            if action == 'k' and len(bits) == 3 and bits[2] in prompts.KINDS:
                await prompts.open_draft(pool, tg, bits[2])
                await bot.send_message(tg, await status_text(pool, tg, bits[2]), parse_mode='HTML',
                                       reply_markup=session_keyboard(False))
                return
            if action == 'menu':
                await prompts.close_draft(pool, tg)
                await bot.send_message(tg, 'Какой промт правим?', parse_mode=None, reply_markup=kinds_keyboard())
                return
            if action == 'exit':
                await prompts.close_draft(pool, tg)
                await bot.send_message(tg, 'Вышли из режима промта. Несохранённые правки сброшены, сохранения на месте.',
                                       parse_mode=None)
                return
            draft = await prompts.draft(pool, tg)
            if action in {'v', 'vt', 'use', 'common'}:
                kind = bits[2] if action == 'common' and len(bits) == 3 else None
                await versions_action(pool, bot, actor, bits, draft, kind)
                return
            if draft is None:
                raise ValueError('Правка закрыта или устарела. Нажмите «✏️ Режим промта» ещё раз.')
            kind = draft['kind']
            if action == 'cur':
                await send_text(bot, tg, f'{prompts.TITLES[kind]} — черновик', draft['body'])
            elif action == 'undo':
                await prompts.update_draft(pool, tg, body=draft['start_body'], note=None, preview_ok=None, previewed_body=None)
                await bot.send_message(tg, 'Правки отменены, текст как в начале.', parse_mode=None,
                                       reply_markup=session_keyboard(False))
            elif action == 'more':
                await bot.send_message(tg, 'Пишите следующую правку.', parse_mode=None,
                                       reply_markup=session_keyboard(draft['body'] != draft['start_body']))
            elif action == 'prev':
                await show_preview(pool, bot, actor, draft)
            elif action == 'check' and kind == 'review':
                await show_check(pool, bot, actor, draft)
            elif action == 'save' and len(bits) == 3 and bits[2] in {'me', 'all'}:
                if draft['previewed_body'] != draft['body']:
                    await bot.send_message(tg, 'Сначала посмотрите пример по этому тексту.', parse_mode=None)
                    await show_preview(pool, bot, actor, draft)
                    return
                if kind == 'review' and bits[2] == 'all' and not checked(tg, draft['body']):
                    await bot.send_message(tg, 'Разбор для всех сохраняется после проверки на звонках: '
                                               'так видно, не поменялись ли итоги у остальных звонков.', parse_mode=None)
                    await show_check(pool, bot, actor, draft)
                    return
                version = await prompts.save(pool, kind, draft['body'], draft['note'], tg, bits[2])
                await prompts.update_draft(pool, tg, start_body=draft['body'])
                where = 'для всех' if bits[2] == 'all' else 'для вас'
                await bot.send_message(
                    tg, f'💾 Сохранено {where}: «{prompts.TITLES[kind]}», сохранение #{version}. '
                        'Вернуться к любому сохранению: «🗂 Сохранения».', parse_mode=None,
                    reply_markup=session_keyboard(False))
                if bits[2] == 'all':
                    await tell_others(pool, bot, actor, kind, version, draft['note'])
            elif action == 'ver':
                await list_versions(pool, bot, actor, kind)
            else:
                raise ValueError('Кнопка устарела.')
        except (Forbidden, ValueError) as exc:
            await bot.send_message(tg, str(exc), parse_mode=None)
        except Exception:
            log.exception('режим промта: сбой кнопки %s', cq.data)
            await bot.send_message(tg, 'Не получилось. Попробуйте ещё раз.', parse_mode=None)

    async def show_preview(pool, bot, actor, draft):
        tg = actor.telegram_user_id
        kind = draft['kind']
        await bot.send_message(tg, 'Собираю пример по черновику, это может занять до минуты…', parse_mode=None)
        text, problem, call_id = await preview(pool, bot, actor, kind, draft['body'])
        await prompts.update_draft(pool, tg, preview_ok=problem is None, previewed_body=draft['body'])
        if problem:
            await bot.send_message(
                tg, f'⚠️ Пример не получился: {problem}.\nЕсли сохранить так, при сбое бот пришлёт причину и стандартную '
                    'версию. Лучше поправить текст ещё раз.', parse_mode=None, reply_markup=save_keyboard(kind))
            return
        title = 'Пример разбора последнего звонка по новому тексту' if kind == 'review' else 'Пример отчёта по новому тексту'
        await send_long(bot, tg, f'<b>👁 {title}</b>\n\n{text}')
        extra = [[Button(text='Как было (сохранённый разбор)', callback_data=f'rv:d:{call_id}')]] if call_id else []
        await bot.send_message(tg, 'Сохранить?', parse_mode=None,
                               reply_markup=Keyboard(inline_keyboard=extra + save_keyboard(kind).inline_keyboard))

    async def show_check(pool, bot, actor, draft):
        tg = actor.telegram_user_id
        await bot.send_message(tg, f'🧪 Разбираю {prompt_check.CALLS} последних звонков старым и новым текстом, '
                                   'это займёт 2–4 минуты…', parse_mode=None)
        report = await prompt_check.check(pool, actor.client_id, draft['body'])
        if not report['broken']:
            _checked[tg] = body_sign(draft['body'])
        await send_long(bot, tg, prompt_check.message(report))
        await bot.send_message(tg, 'Сохранить?', parse_mode=None, reply_markup=save_keyboard('review'))

    async def list_versions(pool, bot, actor, kind):
        tg = actor.telegram_user_id
        who = await names(pool, actor.client_id)
        mine = await prompts.active(pool, kind, tg)
        common = await prompts.active(pool, kind, 0)
        rows = []
        for v in await prompts.versions(pool, kind, tg):
            marks = ('✅ ' if mine and mine['id'] == v['id'] else '') + ('🌐 ' if common and common['id'] == v['id'] else '')
            label = (f"{marks}#{v['id']} {v['created_at'].astimezone(MSK):%d.%m %H:%M} · {scope_word(v['owner_tg'])} · "
                     f"{who.get(v['created_by'], 'бот') if v['created_by'] else 'бот'}: {v['note'] or ''}")
            rows.append([Button(text=label[:64], callback_data=f"pm:v:{v['id']}")])
        if mine:
            rows.append([Button(text='Убрать личную, получать общую', callback_data=f'pm:common:{kind}')])
        await bot.send_message(
            tg, f'🗂 Сохранения «{prompts.TITLES[kind]}». ✅ — действует для вас, 🌐 — для всех '
                '(если ни у одной нет 🌐, для всех действует исходная). Нажмите, чтобы посмотреть или вернуть.',
            parse_mode=None, reply_markup=Keyboard(inline_keyboard=rows))

    async def versions_action(pool, bot, actor, bits, draft, kind):
        tg = actor.telegram_user_id
        if bits[1] == 'common':
            if kind not in prompts.KINDS:
                raise ValueError('Кнопка устарела.')
            await prompts.drop_personal(pool, kind, tg)
            await bot.send_message(tg, f'Личная версия «{prompts.TITLES[kind]}» снята, вы получаете общую.', parse_mode=None)
            if draft and draft['kind'] == kind:
                await prompts.open_draft(pool, tg, kind)
            return
        if len(bits) < 3 or not bits[2].isascii() or not bits[2].isdigit() or len(bits[2]) > 18:
            raise ValueError('Кнопка устарела.')
        row = await prompts.version(pool, int(bits[2]))
        if row is None or row['owner_tg'] not in (0, tg):
            raise ValueError('Сохранение не найдено.')
        kind = row['kind']
        if bits[1] == 'vt':
            await send_text(bot, tg, f"{prompts.TITLES[kind]} — сохранение #{row['id']}", row['body'])
        elif bits[1] == 'v':
            who = (await names(pool, actor.client_id)).get(row['created_by'], 'бот') if row['created_by'] else 'бот'
            await bot.send_message(
                tg, f"Сохранение #{row['id']} «{prompts.TITLES[kind]}», {row['created_at'].astimezone(MSK):%d.%m.%Y %H:%M}, "
                    f"{scope_word(row['owner_tg'])}, автор: {who}.\n{row['note'] or ''}", parse_mode=None,
                reply_markup=kb([('📄 Текст', f"pm:vt:{row['id']}")],
                                [('↩️ Вернуть для себя', f"pm:use:{row['id']}:me")],
                                [('↩️ Вернуть для всех', f"pm:use:{row['id']}:all")]))
        elif bits[1] == 'use' and len(bits) == 4 and bits[3] in {'me', 'all'}:
            await prompts.activate(pool, kind, row['id'], tg, bits[3])
            where = 'для всех' if bits[3] == 'all' else 'для вас'
            await bot.send_message(tg, f"↩️ «{prompts.TITLES[kind]}»: {where} теперь действует сохранение #{row['id']}.",
                                   parse_mode=None)
            if draft and draft['kind'] == kind:
                await prompts.open_draft(pool, tg, kind)
            if bits[3] == 'all':
                await tell_others(pool, bot, actor, kind, row['id'], f"возврат к сохранению #{row['id']}")
        else:
            raise ValueError('Кнопка устарела.')

    async def tell_others(pool, bot, actor, kind, version, note):
        who = (await names(pool, actor.client_id)).get(actor.telegram_user_id, 'Коллега')
        for chat in prompts.editors():
            if chat == actor.telegram_user_id:
                continue
            mine = await prompts.active(pool, kind, chat)
            tail = (' У вас своя личная версия, она остаётся; общую можно включить в «🗂 Сохранения».'
                    if mine else '')
            try:
                await bot.send_message(chat, f'ℹ️ {who} изменил промт «{prompts.TITLES[kind]}» для всех '
                                             f'(сохранение #{version}): {note or "без описания"}.{tail}', parse_mode=None)
            except Exception:
                log.exception('не удалось уведомить редактора %s', chat)
