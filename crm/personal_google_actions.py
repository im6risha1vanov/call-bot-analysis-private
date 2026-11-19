"""Dormant Gmail confirmed-action primitive; host routes and write consent are not wired."""
import base64
import hashlib
import json
import re
import secrets
from email.message import EmailMessage

import aiohttp
from crypto_util import encrypt, decrypt
from crm import personal_google as google

SEND_SCOPE='https://www.googleapis.com/auth/gmail.send'
SEND_ENDPOINT='https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


def payload(data):
    if not isinstance(data,dict) or set(data) != {'to','subject','body'}: raise ValueError('Invalid message')
    to,subject,body=(data[k] for k in ('to','subject','body'))
    if not all(isinstance(x,str) for x in (to,subject,body)): raise ValueError('Invalid message')
    to,subject=to.strip(),subject.strip()
    if not EMAIL.fullmatch(to) or '..' in to or len(to)>254 or not subject or len(subject)>300 or any(c in subject for c in '\r\n') or not body.strip() or len(body)>30000 or '\x00' in body:
        raise ValueError('Invalid message')
    return {'to':to,'subject':subject,'body':body}


def canonical(data):
    return json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'))


async def prepare(conn,tg,client,data):
    """Internal only: caller must first authenticate owner with personal_google.owner."""
    data=payload(data)
    binding=await conn.fetchrow('''SELECT d.version,g.grant_version,g.google_sub,g.email FROM crm_personal_connections d
      JOIN crm_personal_google_write_grants g USING(telegram_user_id,client_id,kind)
      WHERE d.telegram_user_id=$1 AND d.client_id=$2 AND d.kind='mail' AND d.provider='google'
      AND d.version=g.draft_version AND d.email=g.email AND g.scope=$3''',tg,client,SEND_SCOPE)
    if not binding: raise ValueError('Write consent unavailable')
    action_id=secrets.token_urlsafe(24); content=canonical(data); checksum=hashlib.sha256(content.encode()).hexdigest()
    await conn.execute('''INSERT INTO crm_personal_google_actions(id,telegram_user_id,client_id,kind,connection_version,
      grant_version,google_sub,email,payload_enc,payload_hash,status) VALUES($1,$2,$3,'gmail_send',$4,$5,$6,$7,$8,$9,'prepared')''',
      action_id,tg,client,binding['version'],binding['grant_version'],binding['google_sub'],binding['email'],encrypt(content),checksum)
    return {'id':action_id,'payload_hash':checksum,'preview':data,'from':binding['email']}


def mime_raw(data):
    msg=EmailMessage(); msg['To']=data['to']; msg['Subject']=data['subject']; msg.set_content(data['body'])
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


async def send_provider(access_token, data):
    """Exactly one attempt; caller must classify all uncertain outcomes as unknown."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),trust_env=False) as session:
        async with session.post(SEND_ENDPOINT,headers={'Authorization':'Bearer '+access_token},json={'raw':mime_raw(data)},allow_redirects=False) as res:
            if 400 <= res.status < 500 and res.status not in (408,429): return ('rejected',None)
            if res.status != 200: return ('unknown',None)
            raw=await res.content.read(65537)
            if len(raw)>65536: return ('unknown',None)
            try: value=json.loads(raw); identifier=value.get('id')
            except (ValueError,TypeError,AttributeError): return ('unknown',None)
            if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identifier): return ('unknown',None)
            return ('sent',identifier)


async def confirm(conn,tg,client,action_id,confirmed_hash):
    """Call only in an authenticated owner confirmation, while holding the owner lock.

    Never wrap this function in a transaction: claim must commit before the HTTP call.
    A process crash with status 'sending' means unknown; it must never be retried.
    """
    if conn.is_in_transaction(): raise ValueError('Confirmation requires autocommit')
    row=await conn.fetchrow('''UPDATE crm_personal_google_actions a SET status='sending'
       FROM crm_personal_connections d,crm_personal_google_write_grants g
       WHERE a.id=$1 AND a.telegram_user_id=$2 AND a.client_id=$3 AND a.payload_hash=$4
       AND a.status='prepared' AND a.expires_at>now() AND a.kind='gmail_send'
       AND d.telegram_user_id=a.telegram_user_id AND d.client_id=a.client_id AND d.kind='mail'
       AND d.provider='google' AND d.version=a.connection_version AND d.email=a.email
       AND g.telegram_user_id=a.telegram_user_id AND g.client_id=a.client_id AND g.kind='mail'
       AND g.draft_version=a.connection_version AND g.grant_version=a.grant_version
       AND g.google_sub=a.google_sub AND g.email=a.email AND g.scope=$5
       RETURNING a.payload_enc,g.refresh_enc,g.grant_version''',action_id,tg,client,confirmed_hash,SEND_SCOPE)
    if not row: raise ValueError('Action is unavailable or already claimed')
    try:
        data=payload(json.loads(decrypt(row['payload_enc'])))
        if hashlib.sha256(canonical(data).encode()).hexdigest()!=confirmed_hash: raise ValueError('Payload mismatch')
        refresh=decrypt(row['refresh_enc']); client_id,secret,_=google.config()
        token=await google.provider_json('POST',google.TOKEN,data={'client_id':client_id,'client_secret':secret,'refresh_token':refresh,'grant_type':'refresh_token'})
        scope=set(token.get('scope',SEND_SCOPE).split())
        if SEND_SCOPE not in scope or not scope.issubset(google.IDENTITY_SCOPES | {SEND_SCOPE}): raise ValueError('Scope mismatch')
        access=token.get('access_token')
        if not isinstance(access,str) or not access or token.get('token_type','').lower()!='bearer': raise ValueError('Invalid token')
        rotated=token.get('refresh_token')
        if rotated:
            if not isinstance(rotated,str): raise ValueError('Invalid token')
            await conn.execute('UPDATE crm_personal_google_write_grants SET refresh_enc=$4 WHERE telegram_user_id=$1 AND client_id=$2 AND kind=\'mail\' AND grant_version=$3',tg,client,row['grant_version'],encrypt(rotated))
    except Exception:
        await conn.execute("UPDATE crm_personal_google_actions SET status='rejected',completed_at=now() WHERE id=$1 AND status='sending'",action_id)
        return {'status':'rejected'}
    try:
        status,provider_id=await send_provider(access,data)
    except Exception:
        status,provider_id='unknown',None
    await conn.execute("UPDATE crm_personal_google_actions SET status=$2,provider_id=$3,completed_at=now() WHERE id=$1 AND status='sending'",action_id,status,provider_id)
    return {'status':status,'provider_id':provider_id}


def routes(viewer,pool_key):
    """Dormant routes; host must also apply the normal CRM session/CSRF middleware."""
    from aiohttp import web
    from urllib.parse import urlencode

    async def serialized(request, handler):
        if request.headers.get('X-CRM') != '1': raise web.HTTPForbidden()
        pool,tg,client=await google.owner(request,viewer,pool_key)
        async with pool.acquire() as conn:
            key=f'personal-google:{tg}:{client}'
            await conn.execute('SELECT pg_advisory_lock(hashtextextended($1,0))',key)
            request['google_locked_pool']=conn
            try:
                _, current_tg, current_client = await google.owner(request,viewer,pool_key)
                if (current_tg,current_client)!=(tg,client): raise web.HTTPForbidden()
                return await handler(request,conn,tg,client)
            finally:
                await conn.execute('SELECT pg_advisory_unlock(hashtextextended($1,0))',key)

    async def consent_inner(request,conn,tg,client):
        draft=await conn.fetchrow("SELECT provider,email,version FROM crm_personal_connections WHERE telegram_user_id=$1 AND client_id=$2 AND kind='mail'",tg,client)
        if not draft or draft['provider']!='google': raise web.HTTPConflict(reason='Сначала сохраните настройки Google.')
        client_id,_,redirect=google.config(); state=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(64)
        await conn.execute("""INSERT INTO crm_personal_google_states(state_hash,telegram_user_id,client_id,kind,purpose,draft_version,email,verifier_enc,expires_at)
            VALUES($1,$2,$3,'mail','mail_send',$4,$5,$6,now()+interval '10 minutes')""",google.hash_state(state),tg,client,draft['version'],draft['email'],encrypt(verifier))
        url=google.AUTH+'?'+urlencode({'client_id':client_id,'redirect_uri':redirect,'response_type':'code','scope':'openid email '+SEND_SCOPE,
            'state':state,'code_challenge':google.challenge(verifier),'code_challenge_method':'S256','access_type':'offline','prompt':'consent','include_granted_scopes':'false'})
        response=web.json_response({'authorization_url':url}); response.headers['Cache-Control']='no-store'; return response

    async def get_body(request):
        if request.content_length is not None and request.content_length > 50000: raise web.HTTPRequestEntityTooLarge(max_size=50000,actual_size=request.content_length)
        raw=await request.content.read(50001)
        if len(raw)>50000: raise web.HTTPRequestEntityTooLarge(max_size=50000,actual_size=len(raw))
        try: return json.loads(raw)
        except (ValueError,UnicodeError): raise web.HTTPBadRequest(reason='Некорректные данные.') from None

    async def prepare_inner(request,conn,tg,client):
        try: result=await prepare(conn,tg,client,await get_body(request))
        except ValueError: raise web.HTTPBadRequest(reason='Не удалось подготовить письмо.') from None
        response=web.json_response(result); response.headers['Cache-Control']='no-store'; return response

    async def confirm_inner(request,conn,tg,client):
        data=await get_body(request)
        if not isinstance(data,dict) or set(data)!={'id','payload_hash'} or not isinstance(data['id'],str) or not isinstance(data['payload_hash'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}',data['id']) or not re.fullmatch(r'[0-9a-f]{64}',data['payload_hash']):
            raise web.HTTPBadRequest(reason='Некорректное подтверждение.')
        # Revalidate the session and employee immediately before the durable claim.
        _,checked_tg,checked_client=await google.owner(request,viewer,pool_key)
        if (checked_tg,checked_client)!=(tg,client): raise web.HTTPForbidden()
        try: result=await confirm(conn,tg,client,data['id'],data['payload_hash'])
        except ValueError: raise web.HTTPConflict(reason='Письмо недоступно или уже подтверждено.') from None
        response=web.json_response(result); response.headers['Cache-Control']='no-store'; return response

    async def consent_route(request): return await serialized(request,consent_inner)
    async def prepare_route(request): return await serialized(request,prepare_inner)
    async def confirm_route(request): return await serialized(request,confirm_inner)
    return [web.post('/api/personal/google/mail/send-consent',consent_route),
            web.post('/api/personal/google/mail/send/prepare',prepare_route),
            web.post('/api/personal/google/mail/send/confirm',confirm_route)]
