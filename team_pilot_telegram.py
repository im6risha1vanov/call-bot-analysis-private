"""Private Telegram entry points for the team pilot; no outbound integrations."""
import os
from urllib.parse import urlsplit
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from aiohttp import web
from aiogram import F
from aiogram.filters import Command, BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from crm import team_assignments as tasks, team_meetings as meetings, personal_day
from personal_telegram import authorized, ensure_profile, set_state, show_list, keyboard
from tools import Forbidden

MENU = {'☀️ Мой день', '🗓 Планёрки', '📌 Поручения', '🤖 Личный агент', '📋 Личные задачи'}
OLD = {'↩️ Старая методика', '🔄 Вернуть сохранённую версию', 'ℹ️ Версия анализа', '✏️ Режим промта', '📋 Личные задачи', 'Сравнить методики', 'Объединённый разбор'}
STATUS = {'assigned':'Назначено', 'in_progress':'В работе', 'review':'На проверке', 'accepted':'Принято', 'cancelled':'Отменено'}

async def identity(pool, user, chat):
    actor = await authorized(pool, user, chat)
    if not actor or actor.role not in ('owner', 'head', 'manager'):
        raise Forbidden('Командный раздел недоступен.')
    return await tasks.telegram_identity(pool, actor)

def crm_button(route="assignments"):
    url = os.getenv('CRM_URL', '').strip().rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return []
    return [[InlineKeyboardButton(text='Открыть CRM', url=url + '/#team-pilot/' + route)]]

def markup(rows, route="assignments"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows] + crm_button(route))

async def show_assignments(message, pool, ident, mode='assigned'):
    rows = await tasks.list_items(pool, ident, mode)
    buttons = [[(str(r['title'])[:40], f"tp:o:{r['id']}:{r['version']}")] for r in rows[:30]]
    buttons += [[('Мне', 'tp:l:assigned'), ('На проверке', 'tp:l:to_review'), ('Поставленные', 'tp:l:created')], [('➕ Создать поручение', 'tp:new')]]
    await message.answer('Поручения' + ('' if rows else '\nПока нет поручений.') + ('\nПоказаны первые 30; остальные доступны в CRM.' if len(rows)>30 else ''), reply_markup=markup(buttons), parse_mode=None)

async def show_item(message, pool, ident, task_id, version=None):
    r = await tasks.get_item(pool, ident, task_id)
    if version is not None and r['version'] != version: raise ValueError('Поручение изменилось. Откройте его заново.')
    text = [r['title'], 'Статус: ' + STATUS.get(r['status'], r['status'])]
    for key, label in [('description','Описание'), ('due_at','Срок'), ('blocker','Препятствие'), ('result_text','Результат'), ('review_note','Комментарий')]:
        if r.get(key): text.append(label + ': ' + str(r[key]))
    if r.get('result_links'): text.append('Ссылки: ' + ', '.join(r['result_links']))
    rows=[]; suffix=f":{r['id']}:{r['version']}"
    if r['assignee_id']==ident[1] and r['status'] in ('assigned','in_progress'):
        if r['status']=='assigned': rows.append([('Начать', 'tp:a:start'+suffix)])
        rows.append([('Сообщить результат', 'tp:i:submit'+suffix), ('Препятствие', 'tp:i:block'+suffix)])
        if r.get('blocker'): rows.append([('Убрать препятствие', 'tp:a:clear_blocker'+suffix)])
    if r['creator_id']==ident[1] and r['status']=='review':
        rows.append([('Принять', 'tp:a:accept'+suffix), ('Вернуть', 'tp:i:return'+suffix)])
    rows.append([('К поручениям', 'tp:l:assigned')])
    await message.answer('\n'.join(text)[:3900], reply_markup=markup(rows, 'assignment-'+str(task_id)), parse_mode=None)

async def show_meetings(message, pool, ident):
    rows = await meetings.list_items(pool, ident)
    buttons = [[(f"{str(r['series_id'])[:25]} · {str(r['source_updated_at'])[:10]}", f"tp:m:{r['id']}:{r['protocol_version']}")] for r in rows[:30]]
    await message.answer('Планёрки' + ('' if rows else '\nДоступных встреч пока нет.'), reply_markup=markup(buttons, 'meetings'), parse_mode=None)

class PilotInput(BaseFilter):
    def __init__(self, get_pool): self.get_pool=get_pool
    async def __call__(self, message):
        if not message.text or message.text.startswith('/') or message.text in MENU or message.text in OLD: return False
        pool=self.get_pool()
        if not await authorized(pool, message.from_user, message.chat): return False
        return bool(await pool.fetchval("SELECT 1 FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action LIKE 'pilot_%' AND updated_at>now()-interval '1 day'", message.from_user.id))

def install(router, get_pool, head_menu):
    @router.message(Command('menu'))
    @router.message(F.text.in_(MENU | OLD))
    async def menu(message):
        pool=get_pool()
        if message.chat.type != 'private': return
        try: ident=await identity(pool, message.from_user, message.chat)
        except Forbidden:
            await message.answer('Меню обновлено.', reply_markup=await head_menu(message), parse_mode=None); return
        await set_state(pool, message.from_user.id, 'idle')
        text=message.text or ''
        if text.startswith('/menu') or text in OLD:
            await message.answer('Меню обновлено.', reply_markup=await head_menu(message), parse_mode=None)
        if text == '☀️ Мой день':
            actor=await authorized(pool,message.from_user,message.chat)
            profile=await ensure_profile(pool,message.from_user,actor)
            buttons=[[('Личные задачи', 'tp:personal')], [('Поручения', 'tp:l:assigned')]]
            if os.getenv('CRM_PERSONAL_DAY_ENABLED') != '1':
                await message.answer('Раздел «Мой день» пока не включён. Личные задачи доступны ниже.', reply_markup=markup(buttons, 'day'), parse_mode=None)
            else:
                try: zone=ZoneInfo(profile['timezone'])
                except (ValueError, ZoneInfoNotFoundError):
                    await message.answer('Проверьте часовой пояс профиля в CRM.', reply_markup=markup(buttons, 'day'), parse_mode=None); return
                day=await personal_day.sections(pool,ident,profile,zone,datetime.now(zone).date())
                lines=['Мой день · '+day['date']]
                for key,label in [('today','Сегодня'),('overdue','Просрочено'),('blocked','Есть препятствия'),('unscheduled','Без даты')]:
                    lines.append(label+':')
                    lines.extend('• '+str(r['title'])[:120] for r in day[key][:10])
                    if not day[key]: lines.append('—')
                lines.append('Календарь не подключён.')
                await message.answer('\n'.join(lines)[:3900],reply_markup=markup(buttons, 'day'),parse_mode=None)
        elif text == '🗓 Планёрки': await show_meetings(message,pool,ident)
        elif text == '📌 Поручения': await show_assignments(message,pool,ident)
        elif text == '📋 Личные задачи':
            actor=await authorized(pool,message.from_user,message.chat)
            await show_list(message,pool,await ensure_profile(pool,message.from_user,actor))
        elif text == '🤖 Личный агент':
            actor=await authorized(pool,message.from_user,message.chat)
            await ensure_profile(pool,message.from_user,actor)
            await set_state(pool,message.from_user.id,'agent')
            await message.answer('Напишите или запишите поручение. Для выхода: /cancelagent.',parse_mode=None)
        elif text in OLD:
            await message.answer('Эта команда доступна через /analysis_rollback, /analysis_restore, /analysis_status или /prompt.', parse_mode=None)

    @router.callback_query(F.data.startswith('tp:'))
    async def callback(cq):
        pool=get_pool(); message=cq.message
        try:
            ident=await identity(pool,cq.from_user,message.chat if message else None)
            b=cq.data.split(':'); action=b[1]
            if action=='personal' and len(b)==2:
                actor=await authorized(pool,cq.from_user,message.chat)
                await set_state(pool,cq.from_user.id,'idle')
                await show_list(message,pool,await ensure_profile(pool,cq.from_user,actor))
            elif action=='new' and len(b)==2:
                people=await tasks.people(pool,ident)
                buttons=[[(str(p['display_name'] or 'Сотрудник')[:40], 'tp:person:'+str(p['id']))] for p in people[:30]]
                await message.answer('Выберите исполнителя.' if people else 'Нет доступных исполнителей.', reply_markup=markup(buttons), parse_mode=None)
            elif action=='person' and len(b)==3:
                person=int(b[2])
                if person not in {p['id'] for p in await tasks.people(pool,ident)}: raise ValueError('Исполнитель недоступен.')
                if not await set_state(pool,cq.from_user.id,'pilot_create',person,None,'callback:'+cq.id): await cq.answer(); return
                await message.answer('Введите название поручения. Для отмены отправьте /menu. Подробности и срок можно добавить в CRM.',parse_mode=None)
            elif action=='l' and len(b)==3 and b[2] in ('assigned','to_review','created'): await show_assignments(message,pool,ident,b[2])
            elif action=='o' and len(b)==4: await show_item(message,pool,ident,int(b[2]),int(b[3]))
            elif action=='m' and len(b)==4:
                view=await meetings.get_item(pool,ident,int(b[2]))
                if view['protocol_version'] != int(b[3]): raise ValueError('Встреча изменилась. Откройте список снова.')
                agenda=await meetings.agenda_items(pool,ident,view['series_id'])
                lines=['Планёрка: '+str(view['series_id'])]
                for item in view['items'][:20]: lines.append('• '+str(item['text']))
                lines.append('Повестка:')
                lines.extend('• '+str(x['title']) for x in agenda['assignments'][:10])
                lines.extend('• '+str(x['text']) for x in agenda['open_questions'][:10])
                await message.answer('\n'.join(lines)[:3900],reply_markup=markup([], 'meeting-'+b[2]),parse_mode=None)
            elif action in ('i','a') and len(b)==5:
                op=b[2]; task=int(b[3]); version=int(b[4])
                r=await tasks.get_item(pool,ident,task)
                if r['version']!=version: raise ValueError('Поручение изменилось. Откройте его заново.')
                if action=='i' and op in ('submit','block','return'):
                    tasks.transition(r,ident[1],op, {'submit':{'result_text':'проверка','result_links':[]},'block':{'blocker':'проверка'},'return':{'comment':'проверка'}}[op])
                    if not await set_state(pool,cq.from_user.id,'pilot_'+op,task,version,'callback:'+cq.id): await cq.answer(); return
                    await message.answer({'submit':'Введите результат. Ссылки можно добавить отдельными строками после текста.', 'block':'Опишите препятствие.', 'return':'Укажите причину возврата.'}[op]+' Для отмены отправьте /menu.',parse_mode=None)
                elif action=='a' and op in ('start','clear_blocker','accept'):
                    async with pool.acquire() as conn, conn.transaction():
                        await identity(conn,cq.from_user,message.chat)
                        await tasks.change(conn,ident,task,version,op,{},'tg-callback:'+cq.id)
                    await show_item(message,pool,ident,task)
                else: raise ValueError('Кнопка устарела.')
            else: raise ValueError('Кнопка устарела.')
            await cq.answer()
        except (ValueError, Forbidden, web.HTTPException) as exc:
            await cq.answer(str(getattr(exc,'reason',None) or exc)[:180] or 'Действие недоступно.',show_alert=True)

    @router.message(F.text, PilotInput(get_pool))
    async def input_text(message):
        pool=get_pool()
        try:
            ident=await identity(pool,message.from_user,message.chat)
            async with pool.acquire() as conn, conn.transaction():
                await identity(conn,message.from_user,message.chat)
                state=await conn.fetchrow("SELECT * FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action LIKE 'pilot_%' AND updated_at>now()-interval '1 day' FOR UPDATE",message.from_user.id)
                if not state: return
                if not await conn.fetchval('INSERT INTO crm_personal_telegram_events(event_key) VALUES($1) ON CONFLICT DO NOTHING RETURNING 1', f'pilot-message:{message.chat.id}:{message.message_id}'):
                    return
                op=state['action'][6:]; value=message.text.strip()
                if op=='create':
                    if state['task_id'] not in {p['id'] for p in await tasks.people(conn,ident)}: raise ValueError('Исполнитель недоступен.')
                    item=await tasks.create(conn,ident,{'title':value,'assignee_id':state['task_id']},idempotency_key=f'tg-message:{message.chat.id}:{message.message_id}')
                    data=None
                elif op=='submit':
                    lines=value.splitlines(); links=[x.strip() for x in lines if x.strip().startswith(('https://','http://'))]
                    data={'result_text':'\n'.join(x for x in lines if not x.strip().startswith(('https://','http://'))).strip(),'result_links':links}
                elif op=='block': data={'blocker':value}
                elif op=='return': data={'comment':value}
                else: raise ValueError('Ввод устарел. Откройте поручение заново.')
                if op != 'create': item=await tasks.change(conn,ident,state['task_id'],state['version'],op,data,f'tg-message:{message.chat.id}:{message.message_id}')
                await conn.execute("UPDATE crm_personal_telegram_state SET action='idle',updated_at=now() WHERE telegram_user_id=$1 AND token=$2",message.from_user.id,state['token'])
            await message.answer('Сохранено.',parse_mode=None)
            await show_item(message,pool,ident,item['id'])
        except (ValueError, Forbidden, web.HTTPException) as exc:
            await message.answer(str(getattr(exc,'reason',None) or exc) + ' Для отмены отправьте /menu.',parse_mode=None)
