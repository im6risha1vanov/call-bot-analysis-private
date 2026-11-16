"""Personal voice drafts. Audio is bounded and never written to disk."""
import asyncio
import io
import re
import secrets
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from aiogram import F
from aiogram.filters import BaseFilter
from aiohttp import web
from personal_telegram import authorized, ensure_profile, keyboard
from crm.personal import save_task
import deepgram_client

MAX_BYTES = 10 * 1024 * 1024
class BoundedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES: raise ValueError('Голосовое больше 10 МБ.')
        return super().write(data)

WORDS = {'один':1,'час':1,'два':2,'двух':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10,'одиннадцать':11,'двенадцать':12,'тринадцать':13,'четырнадцать':14,'пятнадцать':15,'шестнадцать':16,'семнадцать':17,'восемнадцать':18,'девятнадцать':19,'двадцать':20}

def local_datetime(day, clock, zone):
    tz=ZoneInfo(zone)
    naive=datetime.combine(day,clock)
    first=naive.replace(tzinfo=tz,fold=0)
    second=naive.replace(tzinfo=tz,fold=1)
    if first.utcoffset()!=second.utcoffset() or first.astimezone(ZoneInfo('UTC')).astimezone(tz).replace(tzinfo=None)!=naive:
        raise ValueError('Это местное время неоднозначно или не существует. Уточните время.')
    return first

def extract(transcript, zone, now=None):
    """Parse deadline and reminder clauses separately; ambiguous data stays unset."""
    now = now or datetime.now(ZoneInfo(zone))
    text = transcript.lower().replace('ё', 'е')
    clauses = re.split(r'\bнапомни(?:ть)?\b', text, maxsplit=1)
    def parse(part, fallback_day=None, allow_morning=False):
        days = set()
        if re.search(r'\bсегодня\b', part): days.add(now.date())
        if re.search(r'\bзавтра\b', part): days.add(now.date()+timedelta(days=1))
        if re.search(r'\bпослезавтра\b', part): days.add(now.date()+timedelta(days=2))
        for match in re.finditer(r'\b(20\d{2}-\d{2}-\d{2})\b', part):
            try: days.add(datetime.strptime(match.group(1), '%Y-%m-%d').date())
            except ValueError: pass
        for match in re.finditer(r'\b(\d{1,2}\.\d{1,2}\.20\d{2})\b',part):
            try: days.add(datetime.strptime(match.group(1),'%d.%m.%Y').date())
            except ValueError: pass
        if len(days)>1: return None, None
        day = next(iter(days)) if days else fallback_day
        if not day: return None, None
        matches=list(re.finditer(r'\bв\s+(\d{1,2})(?::(\d{2}))?\b|\bв\s+('+'|'.join(WORDS)+r')\b', part))
        if len(matches)>1: return day, None
        if matches:
            match=matches[0]; hour=int(match.group(1)) if match.group(1) else WORDS[match.group(3)]; minute=int(match.group(2) or 0)
            rest = part[match.end():]
            if re.match(r'\s+(?:'+'|'.join(WORDS)+r'|\d+|без\s+четверти|с\s+половиной)\b',rest): return day,None
            if ('вечера' in part or 'вечером' in part or 'дня' in part) and hour<12: hour+=12
            if 'ночи' in part and hour==12: hour=0
        elif allow_morning and re.search(r'\bутром\b',part): hour,minute=9,0
        else: return day,None
        if hour>23 or minute>59: return day,None
        try: value=local_datetime(day,time(hour,minute),zone)
        except ValueError: return day,None
        return day,value
    day,due=parse(clauses[0])
    reminder=None
    if len(clauses)>1:
        _,reminder=parse(clauses[1],day,allow_morning=True)
    return day,due,reminder

async def wav_from_audio(data):
    process = await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-i', 'pipe:0', '-t', '301', '-ac', '1', '-ar', '16000', '-f', 'wav', 'pipe:1', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(data), timeout=40)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode or not output or len(output) > MAX_BYTES:
        raise ValueError('Не удалось подготовить аудио.')
    return output

class VoiceEligible(BaseFilter):
    def __init__(self, get_pool): self.get_pool=get_pool
    async def __call__(self, message):
        return bool(await authorized(self.get_pool(), message.from_user, message.chat))

class VoiceEdit(BaseFilter):
    def __init__(self, get_pool): self.get_pool=get_pool
    async def __call__(self, message):
        pool=self.get_pool()
        if (message.text or '').startswith('/') or message.text in ('📋 Личные задачи','✏️ Режим промта') or not await authorized(pool,message.from_user,message.chat): return False
        return bool(await pool.fetchval("SELECT 1 FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action IN ('voice_edit_title','voice_edit_due') AND updated_at>now()-interval '1 day'",message.from_user.id))

async def show_preview(message, draft, timezone):
    day, due = draft['planned_day'], draft['due_at']
    if due: due = due.astimezone(ZoneInfo(timezone))
    note = ('Срок: ' + due.strftime('%d.%m.%Y %H:%M %Z')) if due else (('В план на '+day.strftime('%d.%m.%Y')+'; время срока не указано.') if day else 'Дата или время требуют уточнения.')
    reminder = draft['reminder_at']
    reminder_text = reminder.astimezone(ZoneInfo(timezone)).strftime('%d.%m.%Y %H:%M %Z') if reminder else ('09:00 в день срока (или в срок, если он раньше)' if due else 'не задано')
    await message.answer('Черновик задачи:\n' + draft['title'] + '\n\n' + note + '\nНапоминание: ' + reminder_text + '\nПроверьте текст и дату.', parse_mode=None,
        reply_markup=keyboard([[('Сохранить в CRM', 'pv:save:'+draft['token'])],[('Изменить название','pv:title:'+draft['token']),('Изменить срок','pv:due:'+draft['token'])],[('Отменить','pv:cancel:'+draft['token'])]]))
    await message.answer('Расшифровка:\n' + draft['transcript'], parse_mode=None)

def install(router, get_pool):
    @router.message(F.voice, VoiceEligible(get_pool))
    async def voice(message):
        pool = get_pool(); actor = await authorized(pool, message.from_user, message.chat)
        if not actor:
            await message.answer('Голосовой ввод личных задач недоступен.', parse_mode=None); return
        if message.voice.duration > 300 or (message.voice.file_size or 0) > MAX_BYTES:
            await message.answer('Голосовое должно быть не длиннее 5 минут и не больше 10 МБ.', parse_mode=None); return
        profile = await ensure_profile(pool, message.from_user, actor)
        token = secrets.token_hex(8); key = f'voice:{message.chat.id}:{message.message_id}'
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock($1)', message.from_user.id)
            await conn.execute("UPDATE crm_personal_telegram_state SET action='idle' WHERE telegram_user_id=$1 AND action='voice_processing' AND updated_at<now()-interval '5 minutes'",message.from_user.id)
            current = await conn.fetchrow('SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=$1 FOR UPDATE', message.from_user.id)
            if current and current['action'] != 'idle':
                await message.answer('Сначала завершите или отмените текущее редактирование через /tasks.', parse_mode=None); return
            claimed = await conn.fetchval("INSERT INTO crm_personal_voice_drafts(event_key,telegram_user_id,token,status) VALUES($1,$2,$3,'processing') ON CONFLICT DO NOTHING RETURNING 1", key, message.from_user.id, token)
            if not claimed: return
            await conn.execute("INSERT INTO crm_personal_telegram_state(telegram_user_id,action,token) VALUES($1,'voice_processing',$2) ON CONFLICT(telegram_user_id) DO UPDATE SET action='voice_processing',task_id=NULL,version=NULL,token=excluded.token,updated_at=now()", message.from_user.id, token)
        try:
            file = await asyncio.wait_for(message.bot.get_file(message.voice.file_id), timeout=20)
            if file.file_size and file.file_size > MAX_BYTES: raise ValueError('Голосовое больше 10 МБ.')
            buf = BoundedBuffer()
            await asyncio.wait_for(message.bot.download_file(file.file_path, destination=buf, timeout=30), timeout=35)
            wav = await wav_from_audio(buf.getvalue())
            transcript, duration = await asyncio.wait_for(deepgram_client.transcribe_bytes(wav), timeout=90)
            if duration > 300 or not transcript.strip() or len(transcript.strip()) > 4000: raise ValueError('Не удалось получить подходящий текст.')
            transcript = re.sub(r'\[\d{2}:\d{2} Спикер \d+\]\s*', '', transcript).strip()
            if not transcript: raise ValueError('Пустая расшифровка.')
            day, due, explicit_reminder = extract(transcript, profile['timezone'])
            reminder = explicit_reminder
            if day and not due and not reminder:
                reminder = local_datetime(day,time(9),profile['timezone'])
            async with pool.acquire() as conn, conn.transaction():
                state = await conn.fetchrow("SELECT token FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action='voice_processing' FOR UPDATE", message.from_user.id)
                if not state or state['token'] != token:
                    await conn.execute("UPDATE crm_personal_voice_drafts SET status='cancelled',updated_at=now() WHERE token=$1",token); return
                if not await authorized(conn, message.from_user, message.chat):
                    await conn.execute("UPDATE crm_personal_voice_drafts SET status='cancelled',updated_at=now() WHERE token=$1",token)
                    await conn.execute("DELETE FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND token=$2",message.from_user.id,token)
                    return
                draft = await conn.fetchrow("UPDATE crm_personal_voice_drafts SET status='preview',transcript=$2,title=$3,due_at=$4,reminder_at=$5,planned_day=$6,updated_at=now() WHERE token=$1 RETURNING *",token,transcript,transcript[:300],due,reminder,day if not due else None)
                await conn.execute("UPDATE crm_personal_telegram_state SET action='voice_preview',updated_at=now() WHERE telegram_user_id=$1 AND token=$2", message.from_user.id,token)
            await show_preview(message,draft,profile['timezone'])
        except Exception:
            await pool.execute("UPDATE crm_personal_voice_drafts SET status='failed',updated_at=now() WHERE token=$1 AND status='processing'", token)
            await pool.execute("DELETE FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND token=$2 AND action='voice_processing'", message.from_user.id,token)
            await message.answer('Не удалось распознать голосовое. Запишите и отправьте новое сообщение.', parse_mode=None)

    @router.callback_query(F.data.startswith('pv:'))
    async def callback(cq):
        pool=get_pool(); actor=await authorized(pool,cq.from_user,cq.message.chat if cq.message else None)
        if not actor: await cq.answer('Личный раздел недоступен.',show_alert=True); return
        bits=cq.data.split(':')
        if len(bits)!=3 or bits[1] not in ('save','cancel','title','due') or not re.fullmatch('[0-9a-f]{16}',bits[2]):
            await cq.answer('Кнопка устарела.',show_alert=True); return
        token=bits[2]
        draft=await pool.fetchrow("SELECT * FROM crm_personal_voice_drafts WHERE token=$1 AND telegram_user_id=$2 AND status='preview'",token,cq.from_user.id)
        if not draft: await cq.answer('Черновик устарел.',show_alert=True); return
        if bits[1] in ('title','due'):
            changed=await pool.fetchval("UPDATE crm_personal_telegram_state SET action=$3,updated_at=now() WHERE telegram_user_id=$1 AND token=$2 AND action='voice_preview' RETURNING 1",cq.from_user.id,token,'voice_edit_'+bits[1])
            if not changed: await cq.answer('Черновик устарел.',show_alert=True); return
            await cq.answer()
            await cq.message.answer('Введите название.' if bits[1]=='title' else 'Введите срок ГГГГ-ММ-ДД ЧЧ:ММ в часовом поясе профиля или - для очистки.',parse_mode=None)
            return
        if bits[1]=='cancel':
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("UPDATE crm_personal_telegram_state SET action='idle',updated_at=now() WHERE telegram_user_id=$1 AND token=$2",cq.from_user.id,token)
                await conn.execute("UPDATE crm_personal_voice_drafts SET status='cancelled',updated_at=now() WHERE token=$1 AND status='preview'",token)
            await cq.answer('Отменено'); return
        profile=await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1',cq.from_user.id)
        values={'title':draft['title'],'expected_result':draft['transcript'],'due_at':draft['due_at'],'reminder_at':draft['reminder_at'],'planned_day':draft['planned_day']}
        try:
            row=await save_task(pool,profile['id'],values,event_key='callback:'+cq.id,state_identity=(cq.from_user.id,token),voice_token=token)
            if row: await cq.message.answer('Задача сохранена в CRM. Откройте /tasks, чтобы изменить её.',parse_mode=None)
            await cq.answer()
        except web.HTTPException:
            await cq.answer('Черновик устарел. Запишите задачу заново.',show_alert=True)

    @router.message(F.text, VoiceEdit(get_pool))
    async def edit_preview(message):
        pool=get_pool(); actor=await authorized(pool,message.from_user,message.chat)
        if not actor: return
        profile=await pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=$1',message.from_user.id)
        value=message.text.strip()
        try:
            async with pool.acquire() as conn, conn.transaction():
                state=await conn.fetchrow("SELECT * FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action IN ('voice_edit_title','voice_edit_due') FOR UPDATE",message.from_user.id)
                if not state: return
                if state['action']=='voice_edit_title':
                    if not value or len(value)>300: raise ValueError('Название должно содержать от 1 до 300 символов.')
                    draft=await conn.fetchrow("UPDATE crm_personal_voice_drafts SET title=$2,updated_at=now() WHERE token=$1 AND status='preview' RETURNING *",state['token'],value)
                else:
                    entered=None if value=='-' else datetime.strptime(value,'%Y-%m-%d %H:%M')
                    due=None if entered is None else local_datetime(entered.date(),entered.time(),profile['timezone'])
                    draft=await conn.fetchrow("UPDATE crm_personal_voice_drafts SET due_at=$2,planned_day=NULL,reminder_at=NULL,updated_at=now() WHERE token=$1 AND status='preview' RETURNING *",state['token'],due)
                if not draft: raise ValueError('Черновик устарел.')
                await conn.execute("UPDATE crm_personal_telegram_state SET action='voice_preview',updated_at=now() WHERE telegram_user_id=$1 AND token=$2",message.from_user.id,state['token'])
            await show_preview(message,draft,profile['timezone'])
        except ValueError as exc:
            await message.answer(str(exc)+' Проверьте значение.',parse_mode=None)
