"""Dormant, explicitly enabled team knowledge capture. No remote fetching."""
from __future__ import annotations
import os
from datetime import date
from urllib.parse import urlsplit
from aiohttp import web
from . import team_assignments


def text_field(data, key, limit, required=True):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('Некорректное поле: ' + key)
    return value.strip()


def scope_field(data, who):
    scope = data.get('scope', 'private')
    if scope not in ('private', 'shared'):
        raise ValueError('Некорректная область доступа.')
    if scope == 'shared' and who.role != 'director':
        raise web.HTTPForbidden(reason='Общий доступ задаёт директор.')
    return scope


def url_field(data):
    value = text_field(data, 'provenance_url', 2048, False)
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or any(ch.isspace() for ch in value):
        raise ValueError('Некорректная ссылка на источник.')
    return value


def serial(row):
    return {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in dict(row).items()}


def routes(viewer, body, pool_key):
    async def context(request):
        if os.getenv('CRM_TEAM_KNOWLEDGE_ENABLED') != '1':
            raise web.HTTPNotFound()
        who = await viewer(request)
        pool = request.app[pool_key]
        return pool, who, await team_assignments.identity(pool, who)

    async def create_source(request):
        pool, who, ident = await context(request)
        data = await body(request)
        if not isinstance(data, dict) or set(data) - {'title','provenance_url','provenance_check','source_date','scope'}:
            raise ValueError('Некорректные поля источника.')
        title = text_field(data, 'title', 300)
        url = url_field(data)
        check = text_field(data, 'provenance_check', 2000, False)
        raw_date = data.get('source_date')
        try:
            day = date.fromisoformat(raw_date) if raw_date else None
        except (ValueError, TypeError):
            raise ValueError('Некорректная дата источника.')
        scope = scope_field(data, who)
        row = await pool.fetchrow('''INSERT INTO crm_team_knowledge_sources(company_id,owner_id,title,provenance_url,provenance_check,source_date,scope)
             VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *''', *ident,title,url,check,day,scope)
        return web.json_response({'source': serial(row)}, status=201)

    async def sources(request):
        pool, _, ident = await context(request)
        rows = await pool.fetch('''SELECT * FROM crm_team_knowledge_sources WHERE company_id=$1 AND (owner_id=$2 OR scope='shared')
                                  ORDER BY updated_at DESC,id DESC LIMIT 100''', *ident)
        return web.json_response({'sources': [serial(r) for r in rows]})

    async def create_document(request):
        pool, who, ident = await context(request)
        data = await body(request)
        if not isinstance(data, dict) or set(data) - {'source_id','title','body','scope'}:
            raise ValueError('Некорректные поля документа.')
        source_id = data.get('source_id')
        if type(source_id) is not int or source_id < 1:
            raise ValueError('Укажите источник.')
        title, content, scope = text_field(data,'title',300), text_field(data,'body',100000), scope_field(data, who)
        async with pool.acquire() as conn, conn.transaction():
            source = await conn.fetchrow('''SELECT * FROM crm_team_knowledge_sources WHERE id=$1 AND company_id=$2
                    AND (owner_id=$3 OR scope='shared') FOR SHARE''', source_id, *ident)
            if not source:
                raise web.HTTPNotFound(reason='Источник не найден.')
            if scope == 'shared' and source['scope'] != 'shared':
                raise ValueError('Общий документ требует общего источника.')
            row = await conn.fetchrow('''INSERT INTO crm_team_knowledge_documents(company_id,source_id,owner_id,title,body,scope)
                 VALUES($1,$2,$3,$4,$5,$6) RETURNING id,title,scope,source_id,owner_id,created_at,updated_at''', ident[0],source_id,ident[1],title,content,scope)
        return web.json_response({'document': serial(row)},status=201)

    async def listing(request):
        pool, _, ident = await context(request)
        query = request.query.get('q','').strip()
        if len(query) > 200:
            raise ValueError('Слишком длинный запрос.')
        # strpos is literal, so % and _ cannot turn user input into wildcards.
        rows = await pool.fetch('''SELECT d.id,d.title,d.scope,d.owner_id,d.source_id,d.updated_at,
                 s.title AS source_title,s.provenance_url,s.provenance_check,s.source_date,
                 CASE WHEN $3='' THEN left(d.body,300) ELSE substring(d.body FROM greatest(1, strpos(lower(d.body),lower($3))-120) FOR 300) END AS excerpt
             FROM crm_team_knowledge_documents d JOIN crm_team_knowledge_sources s ON s.id=d.source_id AND s.company_id=d.company_id
             WHERE d.company_id=$1 AND (d.owner_id=$2 OR d.scope='shared') AND (s.owner_id=$2 OR s.scope='shared')
             AND ($3='' OR strpos(lower(d.title),lower($3))>0 OR strpos(lower(d.body),lower($3))>0)
             ORDER BY d.updated_at DESC,d.id DESC LIMIT 100''', *ident, query)
        return web.json_response({'documents': [serial(r) for r in rows]})

    async def detail(request):
        pool, _, ident = await context(request)
        row = await pool.fetchrow('''SELECT d.id,d.title,d.body,d.scope,d.owner_id,d.source_id,d.updated_at,
            s.title AS source_title,s.provenance_url,s.provenance_check,s.source_date
            FROM crm_team_knowledge_documents d JOIN crm_team_knowledge_sources s ON s.id=d.source_id AND s.company_id=d.company_id
            WHERE d.id=$1 AND d.company_id=$2 AND (d.owner_id=$3 OR d.scope='shared') AND (s.owner_id=$3 OR s.scope='shared')''', int(request.match_info['id']),*ident)
        if not row:
            raise web.HTTPNotFound(reason='Документ не найден.')
        return web.json_response({'document':serial(row)})

    return [web.post('/api/team/knowledge/sources',create_source), web.get('/api/team/knowledge/sources',sources),
            web.post('/api/team/knowledge/documents',create_document), web.get('/api/team/knowledge/documents',listing),
            web.get('/api/team/knowledge/documents/{id:\\d+}',detail)]
