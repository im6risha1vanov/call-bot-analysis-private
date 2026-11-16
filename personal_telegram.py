"""Private task actions in the existing Averon bot."""
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from aiohttp import web
from aiogram import F
from aiogram.filters import Command, BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from crm.personal import pilot_ids, parse_task, save_task
from tools import resolve_actor

LABELS = {'planned':'Запланировано', 'in_progress':'В работе', 'done':'Готово', 'cancelled':'Отменено'}
FIELDS = {'title':'Название', 'expected_result':'Ожидаемый результат', 'result_note':'Результат', 'blocker':'Что мешает', 'planned_day':'План на день', 'due_at':'Срок', 'reminder_at':'Напомнить'}

def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])

async def authorized(pool, user, chat):
    if not pool or not user or not chat or chat.type != 'private' or chat.id != user.id or user.id not in pilot_ids():
        return None
    actor = await resolve_actor(pool, user.id)
    if not actor or not await pool.fetchval('SELECT 1 FROM employees WHERE telegram_user_id=$1 AND client_id=$2 AND archived_at IS NULL', user.id, actor.client_id):
        return None
    valid = await pool.fetchval('''SELECT 1 FROM crm_invites WHERE telegram_user_id=$1 AND client_id=$2 AND revoked_at IS NULL LIMIT 1''', user.id, actor.client_id)
    if not valid:
        return None
    return actor

class InPersonalMode(BaseFilter):
    def __init__(self, get_pool): self.get_pool = get_pool
    async def __call__(self, message):
        pool = self.get_pool()
        if (message.text or '').startswith('/') or message.text == '✏️ Режим промта': return False
        if not await authorized(pool, message.from_user, message.chat): return False
        return bool(await pool.fetchval("SELECT 1 FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action <> 'idle' AND updated_at > now() - interval '1 day'", message.from_user.id))

async def show_list(message, pool, profile, page=0):
    rows = await pool.fetch('SELECT id,title,status,version FROM crm_personal_tasks WHERE owner_id=$1 ORDER BY id DESC LIMIT 11 OFFSET $2', profile['id'], page * 10)
    more = len(rows) > 10
    rows = rows[:10]
    buttons = [[(f"{r['title'][:35]} · {LABELS[r['status']]}", f"pt:open:{r['id']}:{r['version']}")] for r in rows]
    navigation = []
    if page: navigation.append(('← Назад', f'pt:page:{page-1}'))
    if more: navigation.append(('Далее →', f'pt:page:{page+1}'))
    if navigation: buttons.append(navigation)
    buttons.append([('➕ Новая задача', 'pt:new')])
    heading = 'Личные задачи' if rows else 'Задач пока нет.'
    await message.answer(heading + '\n\nМожно отправить голосовое: одна запись — одна задача. Например: «Завтра в 10 утра подготовить план встречи». Напоминание по сроку по умолчанию в 09:00, либо раньше — в момент срока. Часовой пояс: ' + profile['timezone'], reply_markup=keyboard(buttons), parse_mode=None)

async def show_task(message, pool, profile, task_id):
    task = await pool.fetchrow('SELECT * FROM crm_personal_tasks WHERE owner_id=$1 AND id=$2', profile['id'], task_id)
    if not task:
        await message.answer('Задача недоступна.', parse_mode=None); return
    version = task['version']
    details = [task['title'], 'Статус: ' + LABELS[task['status']]]
    for field, label in FIELDS.items():
        if field != 'title' and task[field]:
            value = task[field].astimezone(ZoneInfo(profile['timezone'])).strftime('%Y-%m-%d %H:%M %Z') if field in ('due_at', 'reminder_at') else task[field]
            details.append(f'{label}: {value}')
    rows = [[(label, f'pt:edit:{field}:{task_id}:{version}')] for field, label in FIELDS.items()]
    rows += [[(label, f'pt:status:{status}:{task_id}:{version}')] for status, label in LABELS.items() if status != task['status']]
    rows.append([('🔔 Выключить напоминания' if task['reminders_enabled'] else '🔔 Включить напоминания', f"pt:reminders:{int(not task['reminders_enabled'])}:{task_id}:{version}")])
    rows.append([('← К задачам', 'pt:list')])
    await message.answer('\n'.join(details)[:3900], reply_markup=keyboard(rows), parse_mode=None)

async def set_state(pool, user_id, action, task_id=None, version=None, event_key=None):
    token = secrets.token_hex(8)
    async with pool.acquire() as conn, conn.transaction():
        if event_key and not await conn.fetchval('INSERT INTO crm_personal_telegram_events(event_key) VALUES($1) ON CONFLICT DO NOTHING RETURNING 1', event_key):
            return False
        await conn.execute('''INSERT INTO crm_personal_telegram_state(telegram_user_id,action,task_id,version,token)
           VALUES($1,$2,$3,$4,$5) ON CONFLICT(telegram_user_id) DO UPDATE SET action=excluded.action,
           task_id=excluded.task_id,version=excluded.version,token=excluded.token,updated_at=now()''', user_id, action, task_id, version, token)
    return True


async def ensure_profile(pool, user, actor):
    profile = await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1', user.id)
    if not profile:
        employee = await pool.fetchrow('SELECT full_name FROM employees WHERE telegram_user_id=$1 AND client_id=$2 AND archived_at IS NULL LIMIT 1', user.id, actor.client_id)
        name = (employee['full_name'] or user.first_name or 'Пользователь').strip()[:200]
        zone = await pool.fetchval("SELECT to_jsonb(c)->>'timezone' FROM clients c WHERE id=$1", actor.client_id) or 'Europe/Moscow'
        try: ZoneInfo(zone)
        except Exception: zone = 'Europe/Moscow'
        profile = await pool.fetchrow('INSERT INTO crm_personal_profiles(telegram_user_id,display_name,timezone) VALUES($1,$2,$3) ON CONFLICT(telegram_user_id) DO NOTHING RETURNING *', user.id, name, zone)
        if profile: pass
        else: profile = await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1', user.id)
    return profile


def install(router, get_pool):
    @router.message(Command('tasks'))
    @router.message(F.text == '📋 Личные задачи')
    async def tasks(message):
        pool = get_pool()
        actor = await authorized(pool, message.from_user, message.chat)
        if not actor:
            await message.answer('Личный раздел недоступен. Если доступ выдан, сначала заполните личный профиль в CRM.', parse_mode=None); return
        profile = await ensure_profile(pool, message.from_user, actor)
        await set_state(pool, message.from_user.id, 'idle')
        await show_list(message, pool, profile)

    @router.callback_query(F.data.startswith('pt:'))
    async def callback(cq):
        pool = get_pool(); message = cq.message
        actor = await authorized(pool, cq.from_user, message.chat if message else None)
        profile = await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1', cq.from_user.id) if actor else None
        if not profile:
            await cq.answer('Личный раздел недоступен.', show_alert=True); return
        bits = cq.data.split(':')
        try:
            action = bits[1]
            if action == 'list' and len(bits) == 2:
                await set_state(pool, cq.from_user.id, 'idle')
                await show_list(message, pool, profile)
            elif action == 'page' and len(bits) == 3 and bits[2].isdigit() and int(bits[2]) <= 100000:
                await show_list(message, pool, profile, int(bits[2]))
            elif action == 'new' and len(bits) == 2:
                if not await set_state(pool, cq.from_user.id, 'create', event_key='callback:' + cq.id):
                    await cq.answer(); return
                await message.answer('Введите название задачи. Для отмены отправьте /tasks.', parse_mode=None)
            elif action == 'open' and len(bits) == 4:
                await show_task(message, pool, profile, int(bits[2]))
            elif action == 'edit' and len(bits) == 5 and bits[2] in FIELDS:
                task_id, version = int(bits[3]), int(bits[4])
                current = await pool.fetchrow('SELECT version FROM crm_personal_tasks WHERE owner_id=$1 AND id=$2', profile['id'], task_id)
                if not current or current['version'] != version: raise ValueError('Кнопка устарела. Откройте задачу снова через /tasks.')
                if not await set_state(pool, cq.from_user.id, bits[2], task_id, version, 'callback:' + cq.id):
                    await cq.answer(); return
                extra = ' Дата: ГГГГ-ММ-ДД.' if bits[2] == 'planned_day' else (' Укажите ГГГГ-ММ-ДД ЧЧ:ММ в часовом поясе профиля.' if bits[2] in ('due_at','reminder_at') else '')
                await message.answer('Введите: ' + FIELDS[bits[2]] + '.' + extra + ' Для отмены отправьте /tasks.', parse_mode=None)
            elif action == 'reminders' and len(bits) == 5 and bits[2] in ('0','1'):
                row = await save_task(pool, profile['id'], {'reminders_enabled':bits[2] == '1'}, int(bits[3]), int(bits[4]), 'callback:' + cq.id)
                if row: await show_task(message, pool, profile, row['id'])
            elif action == 'status' and len(bits) == 5 and bits[2] in LABELS:
                row = await save_task(pool, profile['id'], {'status':bits[2]}, int(bits[3]), int(bits[4]), 'callback:' + cq.id)
                if row: await show_task(message, pool, profile, row['id'])
            else: raise ValueError('Кнопка устарела. Откройте /tasks.')
            await cq.answer()
        except (ValueError, web.HTTPException) as exc:
            await cq.answer(str(getattr(exc, 'reason', None) or exc)[:180], show_alert=True)

    @router.message(F.text, InPersonalMode(get_pool))
    async def input_text(message):
        pool = get_pool(); actor = await authorized(pool, message.from_user, message.chat)
        profile = await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1', message.from_user.id) if actor else None
        if not profile: return
        state = await pool.fetchrow("SELECT * FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND updated_at > now() - interval '1 day'", message.from_user.id)
        if not state: return
        if message.text.startswith('/'):
            await message.answer('Для отмены отправьте /tasks.', parse_mode=None); return
        try:
            action = state['action']; value = message.text.strip()
            if action.startswith('voice_'):
                await message.answer('Используйте кнопки под голосовым черновиком или /tasks для отмены.', parse_mode=None)
                return
            if value == '-' and action != 'create' and action != 'title': value = ''
            if action in ('due_at', 'reminder_at') and value:
                value = datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo(profile['timezone'])).isoformat()
            field = 'title' if action == 'create' else action
            values = parse_task({field:value}, partial=action != 'create')
            row = await save_task(pool, profile['id'], values, state['task_id'], state['version'], f'message:{message.chat.id}:{message.message_id}', (message.from_user.id, state['token']))
            if row:
                await message.answer('Сохранено в CRM.', parse_mode=None)
                await show_task(message, pool, profile, row['id'])
        except (ValueError, web.HTTPException) as exc:
            await message.answer(str(getattr(exc, 'reason', None) or exc) + ' Проверьте значение или откройте /tasks заново.', parse_mode=None)
