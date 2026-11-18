"""Project-scoped, versioned replies. No unapproved audio enters the runtime."""
import base64
import hashlib
import io
import json
import re
import wave

from tools import Forbidden
from . import robot_store


def actor_id(who):
    if who.role != 'director' or who.test or not (who.invite_id or who.actor):
        raise Forbidden('Требуется личный доступ директора.')
    return f'invite:{who.invite_id}' if who.invite_id else f'telegram:{who.actor.telegram_user_id}'


def valid_text(value, label):
    value = ' '.join(str(value or '').split())
    if not value or len(value) > 1000:
        raise ValueError(f'{label}: требуется текст до 1000 символов.')
    return value


def terms(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('Условия должны быть списком до 20 фраз.')
    return [valid_text(item, 'Условие') for item in value]


def voice_signature(op):
    return hashlib.sha256(json.dumps([op.voice, op.voice_role, op.voice_speed], ensure_ascii=False).encode()).hexdigest()


async def projects(pool, owner):
    rows = await pool.fetch('SELECT id,name,operator,profile FROM robot_reply_projects WHERE owner=$1 ORDER BY id', owner)
    result = []
    for row in rows:
        item = dict(row)
        campaigns = await pool.fetch('''SELECT c.id,c.name FROM robot_reply_project_campaigns pc
          JOIN robot_campaigns c ON c.id=pc.campaign_id AND c.owner=$2 WHERE pc.project_id=$1 ORDER BY c.id''', row['id'], owner)
        item['campaigns'] = [dict(c) | {'reply_health': await robot_store.get_option(pool, f"reply_health:{c['id']}", {})} for c in campaigns]
        result.append(item)
    return result


async def create_project(pool, owner, name, operator_id, profile='generic'):
    name = valid_text(name, 'Название')
    if profile not in ('generic', 'packaging'):
        raise ValueError('Неизвестный профиль проекта.')
    if operator_id:
        robot_store._owned(operator_id, owner)
    row = await pool.fetchrow('INSERT INTO robot_reply_projects(owner,name,operator,profile) VALUES($1,$2,$3,$4) RETURNING id,name,operator,profile', owner, name, operator_id or None, profile)
    return dict(row)


async def map_campaign(pool, owner, project_id, campaign_id):
    row = await pool.fetchrow('''SELECT p.operator FROM robot_reply_projects p JOIN robot_campaigns c
      ON c.owner=p.owner AND c.operator=p.operator WHERE p.id=$1 AND p.owner=$2 AND c.id=$3''', project_id, owner, campaign_id)
    if not row:
        raise ValueError('Проект и кампания должны принадлежать кабинету и использовать одного оператора.')
    await pool.execute('INSERT INTO robot_reply_project_campaigns(project_id,campaign_id) VALUES($1,$2) ON CONFLICT(campaign_id) DO UPDATE SET project_id=EXCLUDED.project_id', project_id, campaign_id)
    return {'ok': True}


async def save_contact_memory(pool, owner, project_id, phone, callback_at, actor):
    from .dial import normalize
    from datetime import datetime
    phone = normalize(str(phone or ''))
    if not phone:
        raise ValueError('Укажите корректный номер.')
    try:
        when = datetime.fromisoformat(str(callback_at).replace('Z', '+00:00'))
        if when.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ValueError('Укажите дату перезвона с часовым поясом.')
    saved = await pool.fetchval('''INSERT INTO robot_reply_contact_memory(project_id,phone,callback_at,verified_by)
      SELECT id,$3,$4,$5 FROM robot_reply_projects WHERE id=$1 AND owner=$2
      ON CONFLICT(project_id,phone) DO UPDATE SET callback_at=EXCLUDED.callback_at,verified_by=EXCLUDED.verified_by,updated_at=now()
      RETURNING 1''', project_id, owner, phone, when, actor)
    if not saved:
        raise ValueError('Проект не найден.')
    return {'ok': True}


async def contact_memory(pool, owner, campaign_id, phone):
    row = await pool.fetchrow('''SELECT m.callback_at AS manual_at,m.updated_at AS manual_updated,
      a.callback_at AS auto_at,a.source_at,a.state FROM robot_reply_projects p
      JOIN robot_reply_project_campaigns pc ON pc.project_id=p.id
      JOIN robot_campaigns c ON c.id=pc.campaign_id AND c.owner=p.owner
      LEFT JOIN robot_reply_contact_memory m ON m.project_id=p.id AND m.phone=$3
      LEFT JOIN robot_reply_auto_commitments a ON a.project_id=p.id AND a.phone=$3
      WHERE p.owner=$1 AND c.id=$2 AND NOT EXISTS
        (SELECT 1 FROM robot_stoplist s WHERE s.owner=p.owner AND s.phone=$3)''', owner, campaign_id, phone)
    if not row:
        return {}
    from datetime import datetime, timezone
    if row['state'] == 'withdrawn' and (not row['manual_updated'] or row['source_at'] >= row['manual_updated']):
        return {}
    when = row['manual_at'] or (row['auto_at'] if row['state'] == 'agreed' else None)
    if when and when <= datetime.now(timezone.utc):
        return {}
    if not when:
        return {}
    source_at = row['manual_updated'] if row['manual_at'] else row['source_at']
    return {'callback_at': when.isoformat(), 'callback_source_at': source_at.isoformat()} if source_at else {'callback_at': when.isoformat()}


async def create_candidate(pool, owner, project_id, question, answer, actor):
    question, answer = valid_text(question, 'Вопрос'), valid_text(answer, 'Ответ')
    async with pool.acquire() as conn, conn.transaction():
        candidate_id = await conn.fetchval('INSERT INTO robot_reply_candidates(owner,project_id) SELECT $1,id FROM robot_reply_projects WHERE id=$2 AND owner=$1 RETURNING id', owner, project_id)
        if not candidate_id:
            raise ValueError('Проект не найден.')
        await conn.execute('INSERT INTO robot_reply_versions(candidate_id,version,question,answer) VALUES($1,1,$2,$3)', candidate_id, question, answer)
        await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action) VALUES($1,1,$2,'create')", candidate_id, actor)
    return {'id': candidate_id, 'version': 1}


async def _locked(conn, owner, candidate_id, version):
    row = await conn.fetchrow('''SELECT c.*,p.operator,v.question,v.answer,v.triggers,v.fact_needed,v.fact_note,v.audio_pcm,v.sample_rate,v.voice_signature,
        v.previewed_by,v.approved_by FROM robot_reply_candidates c JOIN robot_reply_projects p ON p.id=c.project_id AND p.owner=c.owner JOIN robot_reply_versions v
        ON v.candidate_id=c.id AND v.version=c.current_version WHERE c.id=$1 AND c.owner=$2 FOR UPDATE OF c''', candidate_id, owner)
    if not row:
        raise ValueError('Предложение не найдено.')
    if row['current_version'] != version:
        raise ValueError('Версия изменилась. Обновите страницу.')
    return row


async def items(pool, owner, project_id):
    rows = await pool.fetch('''SELECT c.id,c.owner,c.project_id,p.operator,c.current_version AS version,c.active_version,c.status,c.source_kind,
      (SELECT count(*)::int FROM robot_reply_learning_links l WHERE l.candidate_id=c.id AND l.owner=c.owner AND l.project_id=c.project_id) AS evidence_count,
      v.question,v.answer,v.triggers,v.next_action,v.fact_needed,v.fact_note,v.context_terms,v.contact_terms,(v.audio_pcm IS NOT NULL) AS has_audio,(v.previewed_at IS NOT NULL) AS previewed,
      v.approved_at,v.rejection_reason FROM robot_reply_candidates c JOIN robot_reply_projects p ON p.id=c.project_id AND p.owner=c.owner JOIN robot_reply_versions v
      ON v.candidate_id=c.id AND v.version=c.current_version WHERE c.owner=$1 AND c.project_id=$2 ORDER BY c.updated_at DESC''', owner,project_id)
    return [dict(r) | {'approved_at': r['approved_at'].isoformat() if r['approved_at'] else None,
             'context_terms': json.loads(r['context_terms']) if isinstance(r['context_terms'], str) else r['context_terms'],
             'triggers': json.loads(r['triggers']) if isinstance(r['triggers'], str) else r['triggers'],
             'contact_terms': json.loads(r['contact_terms']) if isinstance(r['contact_terms'], str) else r['contact_terms']} for r in rows]


async def edit(pool, owner, candidate_id, version, question, answer, actor, context_terms=None, contact_terms=None, triggers=None, next_action='reply', fact_checked=False):
    question, answer = valid_text(question, 'Вопрос'), valid_text(answer, 'Ответ')
    if next_action != 'reply':
        raise ValueError('Это действие пока не поддерживается.')
    async with pool.acquire() as conn, conn.transaction():
        row = await _locked(conn, owner, candidate_id, version)
        new = version + 1
        fact_needed = row['fact_needed'] and fact_checked is not True
        await conn.execute('INSERT INTO robot_reply_versions(candidate_id,version,question,answer,context_terms,contact_terms,triggers,next_action,fact_needed,fact_note) VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10)',candidate_id,new,question,answer,json.dumps(terms(context_terms),ensure_ascii=False),json.dumps(terms(contact_terms),ensure_ascii=False),json.dumps(terms(triggers),ensure_ascii=False),next_action,fact_needed,row['fact_note'] if fact_needed else '')
        await conn.execute("UPDATE robot_reply_candidates SET current_version=$2,status='draft',updated_at=now() WHERE id=$1",candidate_id,new)
        await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action) VALUES($1,$2,$3,'edit')",candidate_id,new,actor)
    return {'id':candidate_id,'version':new}


async def generate(pool, owner, candidate_id, version, actor):
    """Serialize manual and automatic synthesis of one immutable candidate version."""
    async with pool.acquire() as lock_conn:
        key = f'robot-reply-audio:{candidate_id}:{version}'
        acquired = await lock_conn.fetchval('SELECT pg_try_advisory_lock(hashtextextended($1,0))', key)
        if not acquired:
            raise ValueError('Аудио этой версии уже создаётся.')
        try:
            return await _generate_unlocked(lock_conn, owner, candidate_id, version, actor)
        finally:
            await lock_conn.execute('SELECT pg_advisory_unlock(hashtextextended($1,0))', key)


def auto_audio_text_safe(text):
    """Conservative: no literal client quotes, contact details or numeric assertions."""
    text = str(text or '').strip()
    return bool(text and not re.search(r'[\d"«»„“”@\[\]]|https?://', text, re.I))


async def _auto_eligible(conn, row, candidate_id, version):
    if row['source_kind'] not in ('guide', 'learned') or row['status'] != 'draft' or not auto_audio_text_safe(row['answer']):
        return False
    return bool(await conn.fetchval("""SELECT 1 FROM robot_reply_audit WHERE candidate_id=$1 AND version=$2
        AND actor='learning' AND action IN ('draft','refresh')""", candidate_id, version))


async def _generate_unlocked(conn, owner, candidate_id, version, actor):
    import httpx
    from . import robot
    async with conn.transaction():
        row = await _locked(conn,owner,candidate_id,version)
        if row['approved_by'] or row['audio_pcm']:
            raise ValueError('Для нового аудио сначала создайте новую версию.')
        if row['fact_needed']:
            raise ValueError('Сначала проверьте и исправьте неподтверждённые сведения.')
        if actor == 'learning:auto' and not await _auto_eligible(conn, row, candidate_id, version):
            raise ValueError('Версия больше не подходит для автоматической подготовки.')
        if not row['operator']:
            raise ValueError('Сначала выберите оператора проекта.')
        robot_store._owned(row['operator'], owner)
        operator_id = row['operator']
        answer = row['answer']
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.post(f'http://127.0.0.1:{robot.api_port()}/reply-audio', json={'owner':owner, 'operator':operator_id, 'text':answer})
        response.raise_for_status()
        audio = response.json()
    try:
        pcm = base64.b64decode(audio['audio_pcm_b64'], validate=True)
        rate = int(audio['sample_rate'])
        signature = str(audio['voice_signature'])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('Телефонный сервис вернул неверное аудио.') from exc
    if not pcm or len(pcm) % 2 or len(pcm) > 4_000_000:
        raise ValueError('Не удалось получить корректное аудио.')
    if rate < 8000 or rate > 96000 or len(signature) != 64:
        raise ValueError('Телефонный сервис вернул неверные параметры аудио.')
    async with conn.transaction():
        row = await _locked(conn,owner,candidate_id,version)
        if row['audio_pcm'] or row['approved_by'] or row['operator'] != operator_id or row['fact_needed']:
            raise ValueError('Версия или голос изменились. Повторите генерацию.')
        if actor == 'learning:auto' and not await _auto_eligible(conn, row, candidate_id, version):
            raise ValueError('Версия больше не подходит для автоматической подготовки.')
        await conn.execute('UPDATE robot_reply_versions SET audio_pcm=$3,sample_rate=$4,voice_signature=$5 WHERE candidate_id=$1 AND version=$2',candidate_id,version,pcm,rate,signature)
        await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action) VALUES($1,$2,$3,'generate')",candidate_id,version,actor)
    return {'id':candidate_id,'version':version,'has_audio':True}


async def prepare_pending_audio(pool, batch_size=3, owner=None):
    """Prepare a small batch; leases and advisory locks prevent duplicate provider calls."""
    import secrets
    token = secrets.token_hex(16)
    batch_size = max(1, min(int(batch_size), 10))
    await pool.execute('''INSERT INTO robot_reply_audio_jobs(candidate_id,version)
      SELECT c.id,c.current_version FROM robot_reply_candidates c
      JOIN robot_reply_projects p ON p.id=c.project_id AND p.owner=c.owner
      JOIN robot_reply_versions v ON v.candidate_id=c.id AND v.version=c.current_version
      WHERE c.status='draft' AND c.source_kind IN ('guide','learned') AND p.operator IS NOT NULL
        AND ($1::text IS NULL OR c.owner=$1)
        AND p.operator<>'' AND v.audio_pcm IS NULL AND v.approved_at IS NULL AND NOT v.fact_needed
        AND v.answer !~ '[0-9"«»„“”@]' AND position('[' in v.answer)=0 AND position(']' in v.answer)=0 AND v.answer !~* 'https?://'
        AND EXISTS (SELECT 1 FROM robot_reply_audit a WHERE a.candidate_id=c.id AND a.version=c.current_version
                    AND a.actor='learning' AND a.action IN ('draft','refresh'))
        AND NOT EXISTS (SELECT 1 FROM robot_reply_audio_jobs j WHERE j.candidate_id=c.id AND j.version=c.current_version)
      ORDER BY c.id LIMIT 30 ON CONFLICT DO NOTHING''', owner)
    rows = await pool.fetch('''WITH selected AS (
      SELECT j.candidate_id,j.version FROM robot_reply_audio_jobs j
      JOIN robot_reply_candidates c ON c.id=j.candidate_id AND c.current_version=j.version
      JOIN robot_reply_projects p ON p.id=c.project_id AND p.owner=c.owner
      JOIN robot_reply_versions v ON v.candidate_id=c.id AND v.version=j.version
      WHERE c.status='draft' AND c.source_kind IN ('guide','learned') AND p.operator IS NOT NULL AND p.operator<>''
        AND ($3::text IS NULL OR c.owner=$3)
        AND v.audio_pcm IS NULL AND v.approved_at IS NULL AND NOT v.fact_needed
        AND j.next_retry_at<=now() AND (j.lease_until IS NULL OR j.lease_until<now())
      ORDER BY j.next_retry_at,j.candidate_id FOR UPDATE OF j SKIP LOCKED LIMIT $1)
      UPDATE robot_reply_audio_jobs j SET lease_token=$2,lease_until=now()+interval '5 minutes'
      FROM selected s,robot_reply_candidates c WHERE j.candidate_id=s.candidate_id AND j.version=s.version AND c.id=j.candidate_id
      RETURNING j.candidate_id,j.version,c.owner''',batch_size,token,owner)
    succeeded = 0
    for row in rows:
        try:
            await generate(pool,row['owner'],row['candidate_id'],row['version'],'learning:auto')
        except Exception as exc:
            # Store only the exception class, never provider text or personal data.
            await pool.execute('''UPDATE robot_reply_audio_jobs SET attempts=attempts+1,
              next_retry_at=now()+make_interval(secs => LEAST(21600,300*power(2,LEAST(attempts,6)))::int),
              lease_token=NULL,lease_until=NULL,last_error=$4
              WHERE candidate_id=$1 AND version=$2 AND lease_token=$3''',row['candidate_id'],row['version'],token,type(exc).__name__)
        else:
            await pool.execute('DELETE FROM robot_reply_audio_jobs WHERE candidate_id=$1 AND version=$2 AND lease_token=$3',row['candidate_id'],row['version'],token)
            succeeded += 1
    return {'claimed':len(rows),'prepared':succeeded}


async def audio(pool, owner, candidate_id, version, actor):
    async with pool.acquire() as conn, conn.transaction():
        row = await _locked(conn,owner,candidate_id,version)
        if not row['audio_pcm']:
            raise ValueError('Сначала создайте аудио.')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(row['sample_rate']); wav.writeframes(row['audio_pcm'])
        return output.getvalue()


async def preview(pool, owner, candidate_id, version, actor):
    async with pool.acquire() as conn, conn.transaction():
        row = await _locked(conn, owner, candidate_id, version)
        if not row['audio_pcm']:
            raise ValueError('Сначала создайте аудио.')
        await conn.execute('UPDATE robot_reply_versions SET previewed_by=$3,previewed_at=now() WHERE candidate_id=$1 AND version=$2', candidate_id,version,actor)
        await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action) VALUES($1,$2,$3,'preview')", candidate_id,version,actor)
    return {'ok': True}


async def decide(pool, owner, candidate_id, version, actor, action, reason=''):
    async with pool.acquire() as conn, conn.transaction():
        row = await _locked(conn,owner,candidate_id,version)
        if action == 'approve':
            if row['fact_needed']:
                raise ValueError('Сначала проверьте и исправьте неподтверждённые сведения.')
            if not row['audio_pcm'] or row['previewed_by'] != actor:
                raise ValueError('Перед утверждением прослушайте аудио этой версии.')
            triggers = json.loads(row['triggers']) if isinstance(row['triggers'], str) else row['triggers']
            if any(re.search(r'не\s+звон|не\s+беспоко|удалите\s+номер|уберите\s+номер', t, re.I) for t in [row['question']] + list(triggers or [])):
                raise ValueError('Запрет звонков обрабатывается отдельным механизмом и не утверждается как обычный ответ.')
            await conn.execute('UPDATE robot_reply_versions SET approved_by=$3,approved_at=now() WHERE candidate_id=$1 AND version=$2',candidate_id,version,actor)
            await conn.execute("UPDATE robot_reply_candidates SET active_version=$2,status='approved',updated_at=now() WHERE id=$1",candidate_id,version)
        elif action == 'reject':
            if row['active_version'] == version:
                raise ValueError('Нельзя отклонить действующую версию. Создайте новую версию.')
            reason = valid_text(reason, 'Причина')
            await conn.execute('UPDATE robot_reply_versions SET rejection_reason=$3 WHERE candidate_id=$1 AND version=$2',candidate_id,version,reason)
            await conn.execute("UPDATE robot_reply_candidates SET status='rejected',updated_at=now() WHERE id=$1",candidate_id)
        else:
            raise ValueError('Неизвестное действие.')
        await conn.execute('INSERT INTO robot_reply_audit(candidate_id,version,actor,action,detail) VALUES($1,$2,$3,$4,$5)',candidate_id,version,actor,action,reason)
    return {'ok':True,'version':version}


async def rollback(pool, owner, candidate_id, version, target_version, actor):
    async with pool.acquire() as conn, conn.transaction():
        row = await _locked(conn,owner,candidate_id,version)
        old = await conn.fetchrow('SELECT * FROM robot_reply_versions WHERE candidate_id=$1 AND version=$2 AND approved_at IS NOT NULL',candidate_id,target_version)
        if not old or not old['audio_pcm']:
            raise ValueError('Утверждённая версия с текущим голосом не найдена.')
        await conn.execute("UPDATE robot_reply_candidates SET active_version=$2,status='approved',updated_at=now() WHERE id=$1",candidate_id,target_version)
        await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action,detail) VALUES($1,$2,$3,'rollback',$4)",candidate_id,version,actor,str(target_version))
    return {'ok':True,'active_version':target_version}


async def approved_payload(pool, owner, campaign_id, operator_id):
    try:
        robot_store._owned(operator_id,owner)
    except (ValueError, Forbidden):
        return []
    rows = await pool.fetch('''SELECT c.id,v.version,v.question,v.answer,v.triggers,v.next_action,v.context_terms,v.contact_terms,v.audio_pcm,v.sample_rate,v.voice_signature
       FROM robot_reply_candidates c JOIN robot_reply_versions v ON v.candidate_id=c.id AND v.version=c.active_version
       JOIN robot_reply_projects p ON p.id=c.project_id AND p.owner=c.owner AND p.operator=$3
       JOIN robot_reply_project_campaigns pc ON pc.project_id=p.id
       JOIN robot_campaigns campaign ON campaign.id=pc.campaign_id AND campaign.owner=p.owner AND campaign.operator=p.operator
       WHERE c.owner=$1 AND campaign.id=$2 AND v.approved_at IS NOT NULL AND v.audio_pcm IS NOT NULL
       ORDER BY c.id''',owner,campaign_id,operator_id)
    result = []
    total = 0
    for r in rows:
        pcm = r['audio_pcm']
        if not r['voice_signature'] or not 8000 <= r['sample_rate'] <= 96000 or not pcm or len(pcm) % 2:
            raise ValueError('Утверждённое аудио повреждено.')
        total += len(pcm)
        if total > 10_000_000 or len(result) >= 500:
            raise ValueError('Библиотека превышает лимит передачи аудио на звонок.')
        triggers = json.loads(r['triggers']) if isinstance(r['triggers'],str) else r['triggers']
        result.append({'id':r['id'],'version_id':r['version'],'triggers': triggers or [r['question']],'reply':r['answer'], 'next_action':r['next_action'],
                       'voice_signature': r['voice_signature'],
                       'context_terms': json.loads(r['context_terms']) if isinstance(r['context_terms'],str) else r['context_terms'],
                       'contact_terms': json.loads(r['contact_terms']) if isinstance(r['contact_terms'],str) else r['contact_terms'],
                       'audio_pcm_b64':base64.b64encode(pcm).decode('ascii'),'sample_rate':r['sample_rate']})
    return result
