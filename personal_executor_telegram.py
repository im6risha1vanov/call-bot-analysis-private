"""Telegram entry for explicit personal-agent mode."""
import asyncio
import re
from aiogram import F
from aiogram.filters import Command, BaseFilter
from personal_telegram import authorized, ensure_profile, set_state, keyboard
from personal_voice import BoundedBuffer, MAX_BYTES, wav_from_audio
import deepgram_client
import personal_executor as engine

class AgentMode(BaseFilter):
    def __init__(self,get_pool): self.get_pool=get_pool
    async def __call__(self,message):
        if (message.text or '').startswith('/') or message.text in ('📋 Личные задачи','✏️ Режим промта','🤖 Личный агент'): return False
        pool=self.get_pool()
        if not await authorized(pool,message.from_user,message.chat): return False
        return bool(await pool.fetchval("SELECT 1 FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action='agent' AND updated_at>now()-interval '1 day'",message.from_user.id))

def install(router,get_pool):
    @router.message.outer_middleware()
    async def leave_mode(handler,event,data):
        text=event.text or ''
        if ((text.startswith('/') and text.split()[0].split('@')[0] not in ('/agent',)) or text in ('📋 Личные задачи','✏️ Режим промта')) and event.from_user and event.chat.type=='private':
            pool=get_pool()
            if pool: await pool.execute("UPDATE crm_personal_telegram_state SET action='idle',updated_at=now() WHERE telegram_user_id=$1 AND action='agent'",event.from_user.id)
        return await handler(event,data)

    @router.message(Command('agent'))
    @router.message(F.text == '🤖 Личный агент')
    async def start(message):
        pool=get_pool(); actor=await authorized(pool,message.from_user,message.chat)
        if not actor: await message.answer('Личный агент недоступен.',parse_mode=None); return
        await ensure_profile(pool,message.from_user,actor)
        await set_state(pool,message.from_user.id,'agent')
        await message.answer('Напишите или запишите поручение. Я могу работать с личными задачами, звонками CRM и поиском, подготовить материалы или сообщение участнику пилота. Для выхода: /cancelagent.',parse_mode=None)

    @router.message(Command('cancelagent'))
    async def cancel(message):
        pool=get_pool(); actor=await authorized(pool,message.from_user,message.chat)
        if not actor: return
        await set_state(pool,message.from_user.id,'idle')
        await message.answer('Режим личного агента закрыт. Уже подтверждённые действия остаются в истории.',parse_mode=None)

    @router.message(F.text,AgentMode(get_pool))
    async def text_request(message):
        pool=get_pool(); actor=await authorized(pool,message.from_user,message.chat)
        if not actor: return
        try:
            job=await engine.submit(pool,actor,message.text,f'tg:{message.chat.id}:{message.message_id}')
            if job: await message.answer(f"Поручение №{job['id']} принято. Результат появится в личном разделе CRM.",parse_mode=None)
        except ValueError as exc: await message.answer(str(exc),parse_mode=None)

    @router.message(F.voice,AgentMode(get_pool))
    async def voice_request(message):
        pool=get_pool(); actor=await authorized(pool,message.from_user,message.chat)
        if not actor: return
        if message.voice.duration>300 or (message.voice.file_size or 0)>MAX_BYTES:
            await message.answer('Голосовое до 5 минут и 10 МБ.',parse_mode=None); return
        token=await pool.fetchval("SELECT token FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND action='agent'",message.from_user.id)
        if not token: return
        try: row=await engine.submit(pool,actor,'',f'tgvoice:{message.chat.id}:{message.message_id}',status='transcribing')
        except ValueError as exc:
            await message.answer(str(exc),parse_mode=None); return
        if not row: return
        try:
            file=await asyncio.wait_for(message.bot.get_file(message.voice.file_id),timeout=20)
            if file.file_size and file.file_size>MAX_BYTES: raise ValueError()
            buf=BoundedBuffer(); await asyncio.wait_for(message.bot.download_file(file.file_path,destination=buf,timeout=30),timeout=35)
            wav=await wav_from_audio(buf.getvalue()); transcript,duration=await asyncio.wait_for(deepgram_client.transcribe_bytes(wav),timeout=90)
            transcript=re.sub(r'\[\d{2}:\d{2} Спикер \d+\]\s*','',transcript).strip()
            if not transcript or len(transcript)>4000 or duration>300: raise ValueError()
            await engine.actor_for(pool,{'telegram_user_id':actor.telegram_user_id,'client_id':actor.client_id})
            async with pool.acquire() as conn, conn.transaction():
                active=await conn.fetchval("SELECT 1 FROM crm_personal_telegram_state WHERE telegram_user_id=$1 AND token=$2 AND action='agent' FOR UPDATE",message.from_user.id,token)
                if not active:
                    await conn.execute("UPDATE crm_personal_agent_jobs SET status='failed',error='Режим агента закрыт.',updated_at=now() WHERE id=$1",row['id'])
                    return
                await conn.execute("UPDATE crm_personal_agent_jobs SET request=$2,status='queued',updated_at=now() WHERE id=$1 AND status='transcribing'",row['id'],transcript)
            await message.answer(f"Поручение №{row['id']} принято. Результат появится в личном разделе CRM.",parse_mode=None)
        except Exception:
            await pool.execute("UPDATE crm_personal_agent_jobs SET status='failed',error='Не удалось распознать голосовое.',updated_at=now() WHERE id=$1 AND status='transcribing'",row['id'])
            await message.answer('Не удалось распознать голосовое. Отправьте новую запись.',parse_mode=None)

    @router.callback_query(F.data.startswith('pa:'))
    async def confirm(cq):
        pool=get_pool(); actor=await authorized(pool,cq.from_user,cq.message.chat if cq.message else None)
        if not actor: await cq.answer('Недоступно.',show_alert=True); return
        bits=cq.data.split(':')
        if len(bits)!=4 or bits[1] not in ('yes','no') or not bits[2].isdigit() or not re.fullmatch('[0-9a-f]{24}',bits[3]):
            await cq.answer('Кнопка устарела.',show_alert=True); return
        try:
            await engine.approve(pool,actor,int(bits[2]),bits[3],bits[1]=='yes')
            await cq.answer('Подтверждено' if bits[1]=='yes' else 'Отменено')
        except Exception: await cq.answer('Подтверждение устарело.',show_alert=True)
