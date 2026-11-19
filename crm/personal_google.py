"""Dormant Google OAuth foundation. Routes must be explicitly registered by the host."""
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
import secrets
from urllib.parse import urlencode, urlsplit

import aiohttp
from aiohttp import web
from crypto_util import encrypt, decrypt
from crm import personal
from tools import Forbidden

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
USERINFO = 'https://openidconnect.googleapis.com/v1/userinfo'
REVOKE = 'https://oauth2.googleapis.com/revoke'
SCOPES = {'mail': 'https://www.googleapis.com/auth/gmail.readonly',
          'calendar': 'https://www.googleapis.com/auth/calendar.readonly'}
IDENTITY_SCOPES = {'openid', 'email', 'https://www.googleapis.com/auth/userinfo.email'}
# Future separate consent; intentionally excluded from all existing read OAuth flows.
WRITE_SCOPES = {'mail': 'https://www.googleapis.com/auth/gmail.send', 'calendar': 'https://www.googleapis.com/auth/calendar.events'}


def config():
    client = os.environ['CRM_GOOGLE_CLIENT_ID']
    secret = os.environ['CRM_GOOGLE_CLIENT_SECRET']
    redirect = os.environ['CRM_GOOGLE_REDIRECT_URI']
    p = urlsplit(redirect)
    if not client or not secret or p.scheme != 'https' or not p.hostname or p.username or p.query or p.fragment or p.path != '/api/personal/google/callback':
        raise ValueError('Invalid Google configuration')
    return client, secret, redirect


def hash_state(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')


def validate_token(data, kind, purpose='read'):
    if not isinstance(data, dict) or not isinstance(data.get('access_token'), str) or not data['access_token']:
        raise ValueError('Google token response invalid')
    granted = set(data.get('scope', '').split())
    required = WRITE_SCOPES['mail'] if purpose == 'mail_send' and kind == 'mail' else SCOPES[kind]
    if required not in granted or not granted.issubset(IDENTITY_SCOPES | {required}):
        raise ValueError('Google scope mismatch')
    if not isinstance(data.get('refresh_token'), str) or not data['refresh_token']:
        raise ValueError('Google refresh token missing; reconnect with consent')
    if data.get('token_type', '').lower() != 'bearer':
        raise ValueError('Google token type invalid')


async def provider_json(method, url, *, data=None, token=None):
    # Endpoints are constants, redirects and proxy environment are disabled.
    if url not in (TOKEN, USERINFO, REVOKE):
        raise ValueError('Invalid provider endpoint')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.request(method, url, data=data, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('Google request failed')
            if url == REVOKE:
                return {}
            if response.content_length and response.content_length > 65536:
                raise ValueError('Google response too large')
            raw = await response.content.read(65537)
            if len(raw) > 65536:
                raise ValueError('Google response too large')
            return json.loads(raw)


GMAIL_LIST = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
CALENDAR_EVENTS = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
CALENDAR_FREEBUSY = 'https://www.googleapis.com/calendar/v3/freeBusy'
MESSAGE_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


async def read_provider(method, url, token, *, params=None, body=None):
    valid_message = url.startswith(GMAIL_LIST + '/') and bool(MESSAGE_ID.fullmatch(url[len(GMAIL_LIST)+1:]))
    if not ((method == 'GET' and (url in (GMAIL_LIST, CALENDAR_EVENTS) or valid_message)) or
            (method == 'POST' and url == CALENDAR_FREEBUSY)):
        raise ValueError('Provider path is not allowed')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=False) as session:
        async with session.request(method, url, headers={'Authorization':'Bearer '+token}, params=params,
                                   json=body, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('Provider read failed')
            raw = await response.content.read(1_000_001)
            if len(raw)>1_000_000: raise ValueError('Provider response too large')
            return json.loads(raw)


def bounded_text(value, limit):
    return value[:limit] if isinstance(value, str) else ''


def time_window(request):
    start, end = request.query.get('from',''), request.query.get('to','')
    try:
        a, b = (datetime.fromisoformat(v.replace('Z','+00:00')) for v in (start, end))
        if a.utcoffset() is None or b.utcoffset() is None or b <= a or b-a > timedelta(days=31):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise web.HTTPBadRequest(reason='Укажите интервал до 31 дня с часовым поясом.') from None
    return start, end


async def refresh_token(conn, tg, client, kind):
    """Internal only: caller must hold the same per-owner advisory lock as routes."""
    if kind not in SCOPES:
        raise ValueError('Invalid kind')
    row = await conn.fetchrow('''SELECT t.token_enc,t.draft_version FROM crm_personal_google_tokens t
      JOIN crm_personal_connections d USING (telegram_user_id,client_id,kind)
      WHERE t.telegram_user_id=$1 AND t.client_id=$2 AND t.kind=$3 AND d.provider='google'
      AND d.version=t.draft_version AND d.email=t.email''', tg, client, kind)
    if not row:
        raise ValueError('Google connection unavailable')
    stored = json.loads(decrypt(row['token_enc']))
    client_id, secret, _ = config()
    result = await provider_json('POST', TOKEN, data={'client_id':client_id,'client_secret':secret,
        'refresh_token':stored['refresh_token'],'grant_type':'refresh_token'})
    if not isinstance(result.get('access_token'),str) or not result['access_token'] or result.get('token_type','').lower() != 'bearer':
        raise ValueError('Google refresh failed')
    if 'scope' in result:
        granted = set(result['scope'].split())
        if SCOPES[kind] not in granted or not granted.issubset(IDENTITY_SCOPES | {SCOPES[kind]}):
            raise ValueError('Google scope mismatch')
    if result.get('refresh_token'):
        if not isinstance(result['refresh_token'],str):
            raise ValueError('Google refresh token invalid')
        await conn.execute('UPDATE crm_personal_google_tokens SET token_enc=$4,updated_at=now() WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3',
            tg,client,kind,encrypt(json.dumps({'refresh_token':result['refresh_token']})))
    return result['access_token']


async def owner(request, viewer, pool_key):
    pool = request.get('google_locked_pool', request.app[pool_key])
    who = await viewer(request)
    tg = await personal.identity(pool, who)
    if tg is None or not await pool.fetchval('SELECT 1 FROM employees WHERE telegram_user_id=$1 AND client_id=$2 AND archived_at IS NULL', tg, who.actor.client_id):
        raise Forbidden('Личный раздел недоступен.')
    return pool, tg, who.actor.client_id


def routes(viewer, pool_key):
    async def start(request):
        pool, tg, client = await owner(request, viewer, pool_key)
        kind = request.match_info['kind']
        if kind not in SCOPES: raise web.HTTPNotFound()
        row = await pool.fetchrow('SELECT provider,email,version FROM crm_personal_connections WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3', tg, client, kind)
        if not row or row['provider'] != 'google': raise web.HTTPConflict(reason='Сначала сохраните настройки Google.')
        client_id, _, redirect = config()
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        await pool.execute('''INSERT INTO crm_personal_google_states(state_hash,telegram_user_id,client_id,kind,draft_version,email,verifier_enc,expires_at)
          VALUES($1,$2,$3,$4,$5,$6,$7,now()+interval '10 minutes')''', hash_state(state), tg, client, kind, row['version'], row['email'], encrypt(verifier))
        url = AUTH + '?' + urlencode({'client_id': client_id, 'redirect_uri': redirect, 'response_type':'code',
            'scope':'openid email ' + SCOPES[kind], 'state':state, 'code_challenge':challenge(verifier),
            'code_challenge_method':'S256', 'access_type':'offline', 'prompt':'consent', 'include_granted_scopes':'false'})
        response = web.json_response({'authorization_url':url}); response.headers['Cache-Control']='no-store'; return response

    async def callback(request):
        pool, tg, client = await owner(request, viewer, pool_key)
        state, code = request.query.get('state',''), request.query.get('code','')
        if not state or len(state)>128 or not state.isascii() or not code or len(code)>4096: raise web.HTTPBadRequest()
        saved = await pool.fetchrow('''DELETE FROM crm_personal_google_states WHERE state_hash=$1 AND telegram_user_id=$2
          AND client_id=$3 AND expires_at > now() RETURNING *''', hash_state(state), tg, client)
        if not saved: raise web.HTTPBadRequest(reason='Авторизация истекла или уже использована.')
        draft = await pool.fetchrow('SELECT provider,email,version FROM crm_personal_connections WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3', tg, client, saved['kind'])
        if not draft or draft['provider'] != 'google' or draft['version'] != saved['draft_version'] or draft['email'] != saved['email']:
            raise web.HTTPConflict(reason='Настройки изменились; начните авторизацию заново.')
        client_id, secret, redirect = config()
        try:
            data = await provider_json('POST', TOKEN, data={'code':code,'client_id':client_id,'client_secret':secret,
                'redirect_uri':redirect,'grant_type':'authorization_code','code_verifier':decrypt(saved['verifier_enc'])})
            validate_token(data, saved['kind'], saved['purpose'])
            info = await provider_json('GET', USERINFO, token=data['access_token'])
            if not isinstance(info.get('sub'), str) or not info['sub'] or info.get('email_verified') is not True or info.get('email','').casefold() != saved['email'].casefold():
                raise ValueError('identity mismatch')
        except (ValueError, KeyError, TypeError, aiohttp.ClientError, TimeoutError):
            raise web.HTTPBadRequest(reason='Не удалось подтвердить учётную запись Google.') from None
        if saved['purpose'] == 'mail_send':
            if saved['kind'] != 'mail': raise web.HTTPBadRequest()
            existing = await pool.fetchrow("SELECT google_sub FROM crm_personal_google_tokens WHERE telegram_user_id=$1 AND client_id=$2 AND kind='mail' AND draft_version=$3",tg,client,saved['draft_version'])
            if existing and existing['google_sub'] != info['sub']:
                raise web.HTTPBadRequest(reason='Учётная запись Google не совпадает.')
            result = await pool.fetchrow('''INSERT INTO crm_personal_google_write_grants(telegram_user_id,client_id,kind,draft_version,google_sub,email,scope,refresh_enc,grant_version)
              SELECT $1,$2,'mail',$3,$4,$5,$6,$7,$8 FROM crm_personal_connections WHERE telegram_user_id=$1 AND client_id=$2 AND kind='mail' AND provider='google' AND version=$3 AND email=$5
              ON CONFLICT(telegram_user_id,client_id,kind) DO UPDATE SET draft_version=EXCLUDED.draft_version,google_sub=EXCLUDED.google_sub,email=EXCLUDED.email,scope=EXCLUDED.scope,refresh_enc=EXCLUDED.refresh_enc,grant_version=EXCLUDED.grant_version RETURNING kind''',tg,client,saved['draft_version'],info['sub'],saved['email'],WRITE_SCOPES['mail'],encrypt(data['refresh_token']),secrets.token_urlsafe(24))
            if not result: raise web.HTTPConflict(reason='Настройки изменились; начните авторизацию заново.')
            response=web.json_response({'connected':True,'kind':'mail','purpose':'mail_send'}); response.headers['Cache-Control']='no-store'; response.headers['Referrer-Policy']='no-referrer'; return response
        token_json = json.dumps({'refresh_token':data['refresh_token']})
        # Recheck draft at write time to avoid accepting a changed identity during provider requests.
        result = await pool.fetchrow('''INSERT INTO crm_personal_google_tokens(telegram_user_id,client_id,kind,draft_version,google_sub,email,token_enc)
          SELECT $1,$2,$3,$4,$5,$6,$7 FROM crm_personal_connections WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3 AND provider='google' AND version=$4 AND email=$6
          ON CONFLICT(telegram_user_id,client_id,kind) DO UPDATE SET draft_version=EXCLUDED.draft_version,google_sub=EXCLUDED.google_sub,email=EXCLUDED.email,token_enc=EXCLUDED.token_enc,updated_at=now() RETURNING kind''',
          tg,client,saved['kind'],saved['draft_version'],info['sub'],saved['email'],encrypt(token_json))
        if not result: raise web.HTTPConflict(reason='Настройки изменились; начните авторизацию заново.')
        response=web.json_response({'connected':True,'kind':saved['kind']}); response.headers['Cache-Control']='no-store'; response.headers['Referrer-Policy']='no-referrer'; return response

    async def disconnect(request):
        pool,tg,client=await owner(request,viewer,pool_key)
        kind=request.match_info['kind']
        if kind not in SCOPES: raise web.HTTPNotFound()
        row=await pool.fetchrow('DELETE FROM crm_personal_google_tokens WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3 RETURNING token_enc',tg,client,kind)
        await pool.execute('DELETE FROM crm_personal_google_states WHERE telegram_user_id=$1 AND client_id=$2 AND kind=$3',tg,client,kind)
        write_row = None
        if kind == 'mail':
            write_row = await pool.fetchrow("DELETE FROM crm_personal_google_write_grants WHERE telegram_user_id=$1 AND client_id=$2 AND kind='mail' RETURNING refresh_enc",tg,client)
        revoked = False
        if row:
            try:
                token=json.loads(decrypt(row['token_enc']))['refresh_token']
                await provider_json('POST',REVOKE,data={'token':token}); revoked=True
            except (ValueError, KeyError, TypeError, aiohttp.ClientError, TimeoutError):
                pass
        write_revoked = None
        if write_row:
            try:
                await provider_json('POST',REVOKE,data={'token':decrypt(write_row['refresh_enc'])}); write_revoked=True
            except (ValueError, KeyError, TypeError, aiohttp.ClientError, TimeoutError):
                write_revoked=False
        return web.json_response({'disconnected':True,'provider_revoked':revoked if row else None,'write_provider_revoked':write_revoked})
    async def mail_search(request):
        pool,tg,client=await owner(request,viewer,pool_key)
        query=request.query.get('q','').strip()
        if len(query)>500: raise web.HTTPBadRequest(reason='Слишком длинный запрос.')
        try:
            token=await refresh_token(pool,tg,client,'mail')
            listing=await read_provider('GET',GMAIL_LIST,token,params={'q':query,'maxResults':10})
            messages=[]
            for item in listing.get('messages',[])[:10]:
                mid=item.get('id','')
                if not isinstance(mid,str) or not MESSAGE_ID.fullmatch(mid): raise ValueError('Invalid message id')
                value=await read_provider('GET',GMAIL_LIST+'/'+mid,token,params=[('format','metadata'),('metadataHeaders','From'),('metadataHeaders','To'),('metadataHeaders','Subject'),('metadataHeaders','Date')])
                headers={}
                for h in value.get('payload',{}).get('headers',[])[:100]:
                    name=str(h.get('name','')).lower()
                    if name in ('from','to','subject','date') and name not in headers:
                        headers[name]=bounded_text(h.get('value'),500)
                messages.append({'id':mid,'headers':headers,'snippet':bounded_text(value.get('snippet'),500)})
        except (ValueError, KeyError, TypeError, aiohttp.ClientError, TimeoutError):
            raise web.HTTPBadGateway(reason='Не удалось получить письма Google.') from None
        response=web.json_response({'messages':messages}); response.headers['Cache-Control']='no-store'; return response

    async def calendar(request):
        pool,tg,client=await owner(request,viewer,pool_key)
        start,end=time_window(request)
        mode=request.match_info['mode']
        if mode not in ('events','freebusy'): raise web.HTTPNotFound()
        try:
            token=await refresh_token(pool,tg,client,'calendar')
            if mode=='events':
                data=await read_provider('GET',CALENDAR_EVENTS,token,params={'timeMin':start,'timeMax':end,'maxResults':100,'singleEvents':'true','orderBy':'startTime'})
                events=[]
                for e in data.get('items',[])[:100]:
                    events.append({'id':bounded_text(e.get('id'),128),'summary':bounded_text(e.get('summary'),300),
                        'start':{k:bounded_text(e.get('start',{}).get(k),64) for k in ('date','dateTime','timeZone') if k in e.get('start',{})},'end':{k:bounded_text(e.get('end',{}).get(k),64) for k in ('date','dateTime','timeZone') if k in e.get('end',{})},'status':bounded_text(e.get('status'),50)})
                result={'events':events,'has_more':bool(data.get('nextPageToken'))}
            else:
                data=await read_provider('POST',CALENDAR_FREEBUSY,token,body={'timeMin':start,'timeMax':end,'items':[{'id':'primary'}]})
                primary=data.get('calendars',{}).get('primary',{})
                if primary.get('errors'): raise ValueError('Calendar availability unavailable')
                result={'busy':[{'start':bounded_text(e.get('start'),64),'end':bounded_text(e.get('end'),64)} for e in primary.get('busy',[])[:500]]}
        except (ValueError, KeyError, TypeError, aiohttp.ClientError, TimeoutError):
            raise web.HTTPBadGateway(reason='Не удалось получить календарь Google.') from None
        response=web.json_response(result); response.headers['Cache-Control']='no-store'; return response

    def serialized(handler):
        async def invoke(request):
            pool, tg, client = await owner(request, viewer, pool_key)
            # Session advisory lock serializes start/callback/disconnect without rolling
            # back consumed states when a provider request fails.
            async with pool.acquire() as conn:
                key = f'personal-google:{tg}:{client}'
                await conn.execute('SELECT pg_advisory_lock(hashtextextended($1,0))', key)
                request['google_locked_pool'] = conn
                try:
                    return await handler(request)
                finally:
                    await conn.execute('SELECT pg_advisory_unlock(hashtextextended($1,0))', key)
        return invoke
    return [web.post('/api/personal/google/{kind}/start',serialized(start)), web.get('/api/personal/google/callback',serialized(callback)), web.delete('/api/personal/google/{kind}',serialized(disconnect)), web.get('/api/personal/google/mail/search',serialized(mail_search)), web.get('/api/personal/google/calendar/{mode}',serialized(calendar))]
