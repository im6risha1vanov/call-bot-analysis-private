from aiohttp import web
from . import robot_reply_library as lib
from . import robot_store as rs


def routes(viewer, body, pool_key):
    async def context(request, data=None, write=False):
        who = await viewer(request)
        actor = lib.actor_id(who) if write else None
        if who.role != 'director' or who.test:
            from tools import Forbidden
            raise Forbidden('Раздел директора.')
        pool = request.app[pool_key]
        owner = await rs.owner_for(pool, who, (data or {}).get('owner') if data is not None else request.query.get('owner'), write)
        return pool, owner, actor

    async def listing(request):
        pool, owner, _ = await context(request)
        return web.json_response({'items': await lib.items(pool, owner, int(request.query.get('project_id') or 0))})

    async def project_list(request):
        pool, owner, _ = await context(request)
        return web.json_response({'items': await lib.projects(pool, owner)})

    async def project_create(request):
        data = await body(request)
        pool, owner, _ = await context(request, data, True)
        return web.json_response(await lib.create_project(pool, owner, data.get('name'), data.get('operator'), data.get('profile', 'generic')))

    async def project_operator(request):
        data = await body(request)
        pool, owner, _ = await context(request, data, True)
        operator_id = str(data.get('operator') or '')
        rs._owned(operator_id, owner)
        updated = await pool.fetchval('UPDATE robot_reply_projects SET operator=$3 WHERE id=$1 AND owner=$2 RETURNING id', int(request.match_info['id']), owner, operator_id)
        if not updated:
            raise ValueError('Проект не найден.')
        return web.json_response({'ok': True})

    async def project_profile(request):
        data = await body(request)
        pool, owner, _ = await context(request, data, True)
        profile = data.get('profile')
        if profile not in ('generic', 'packaging'):
            raise ValueError('Неизвестный профиль проекта.')
        updated = await pool.fetchval('''UPDATE robot_reply_projects p SET profile=$3 WHERE p.id=$1 AND p.owner=$2
          AND NOT EXISTS (SELECT 1 FROM robot_reply_candidates c WHERE c.project_id=p.id)
          AND NOT EXISTS (SELECT 1 FROM robot_reply_learning_sources s WHERE s.project_id=p.id)
          AND NOT EXISTS (SELECT 1 FROM robot_reply_learning_scripts s WHERE s.project_id=p.id)
          RETURNING id''', int(request.match_info['id']), owner, profile)
        if not updated:
            raise ValueError('Профиль можно изменить только у пустого проекта.')
        return web.json_response({'ok': True})

    async def campaign_map(request):
        data = await body(request)
        pool, owner, _ = await context(request, data, True)
        return web.json_response(await lib.map_campaign(pool, owner, int(request.match_info['id']), int(data.get('campaign_id') or 0)))

    async def script_map(request):
        data = await body(request)
        pool, owner, actor = await context(request, data, True)
        who = await viewer(request)
        project = int(request.match_info['id'])
        script = lib.valid_text(data.get('script'), 'Сценарий')
        if not who.company:
            raise ValueError('Компания не определена.')
        result = await pool.fetchval('''INSERT INTO robot_reply_learning_scripts(owner,project_id,client_id,script,verified_by)
          SELECT $1,id,$3,$4,$5 FROM robot_reply_projects WHERE id=$2 AND owner=$1
          ON CONFLICT(owner,project_id,client_id,script) DO UPDATE SET verified_by=EXCLUDED.verified_by RETURNING 1''',
          owner, project, who.company, script, actor)
        if not result:
            raise ValueError('Проект не найден.')
        return web.json_response({'ok': True})

    async def sources(request):
        import json
        from .robot_reply_learning import hypotheses, coverage
        pool, owner, _ = await context(request)
        project = int(request.match_info['id'])
        rows = await pool.fetch('''SELECT s.source_type,s.source_id,s.status,s.reason,s.outcome,s.evidence
          FROM robot_reply_learning_sources s JOIN robot_reply_projects p ON p.id=s.project_id AND p.owner=s.owner
          WHERE s.owner=$1 AND s.project_id=$2 ORDER BY s.updated_at DESC LIMIT 1000''', owner, project)
        items = []
        for row in rows:
            item = dict(row)
            if isinstance(item['evidence'], str):
                item['evidence'] = json.loads(item['evidence'])
            items.append(item)
        counts = await pool.fetch('''SELECT source_type,metadata->>'operator' AS operator,status, count(*)::int AS count
          FROM robot_reply_learning_sources WHERE owner=$1 AND project_id=$2
          GROUP BY source_type,metadata->>'operator',status ORDER BY source_type,operator,status''', owner, project)
        total = sum(row['count'] for row in counts)
        return web.json_response({'items': items, 'hypotheses': hypotheses(items), 'total': total, 'shown': len(items),
                                  'coverage': await coverage(pool, owner, project), 'operator_coverage': [dict(r) for r in counts]})

    async def metrics(request):
        pool, owner, _ = await context(request)
        project = int(request.match_info['id'])
        rows = await pool.fetch('''SELECT c.id AS reply_id,v.version,
          count(e.event)::int AS uses,
          count(e.event) FILTER (WHERE e.event->>'outcome'='interrupted')::int AS interrupted,
          count(e.event) FILTER (WHERE e.repeated > 1)::int AS uses_in_repeated_calls,
          count(e.event) FILTER (WHERE e.call_outcome='interested')::int AS calls_interested_events,
          count(e.event) FILTER (WHERE e.call_outcome='callback')::int AS calls_callback_events,
          count(e.event) FILTER (WHERE e.event->>'first_audio_ms' ~ '^[0-9]{1,6}$')::int AS first_audio_samples,
          avg(CASE WHEN e.event->>'first_audio_ms' ~ '^[0-9]{1,6}$' THEN (e.event->>'first_audio_ms')::int END)::float AS mean_first_audio_ms,
          avg(CASE WHEN e.event->>'response_ms' ~ '^[0-9]{1,6}$' THEN (e.event->>'response_ms')::int END)::float AS mean_response_ms
          FROM robot_reply_candidates c JOIN robot_reply_versions v ON v.candidate_id=c.id
          LEFT JOIN (SELECT r.owner,r.outcome AS call_outcome,ev AS event,
            count(*) OVER (PARTITION BY r.call_id,ev->>'reply_id') AS repeated FROM robot_calls r
            CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(r.data->'reply_library_events')='array'
              THEN r.data->'reply_library_events' ELSE '[]'::jsonb END) ev) e
          ON e.owner=c.owner AND e.event->>'reply_id'=c.id::text AND e.event->>'version_id'=v.version::text
          WHERE c.owner=$1 AND c.project_id=$2 GROUP BY c.id,v.version ORDER BY c.id,v.version''', owner, project)
        baseline = await pool.fetchrow('''SELECT count(*)::int AS samples,
          count(*) FILTER (WHERE ev->>'first_audio_ms' ~ '^[0-9]{1,6}$')::int AS first_audio_samples,
          avg(CASE WHEN ev->>'response_ms' ~ '^[0-9]{1,6}$' THEN (ev->>'response_ms')::int END)::float AS mean_response_ms,
          avg(CASE WHEN ev->>'first_audio_ms' ~ '^[0-9]{1,6}$' THEN (ev->>'first_audio_ms')::int END)::float AS mean_first_audio_ms
          FROM robot_calls r JOIN robot_reply_project_campaigns pc ON pc.campaign_id=r.campaign_id
          JOIN robot_reply_projects p ON p.id=pc.project_id AND p.owner=r.owner
          CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(r.data->'reply_library_events')='array'
            THEN r.data->'reply_library_events' ELSE '[]'::jsonb END) ev
          WHERE p.owner=$1 AND p.id=$2 AND ev->>'outcome'='fallback' ''', owner, project)
        return web.json_response({'items': [dict(r) for r in rows], 'fallback': dict(baseline)})

    async def propose(request):
        from .robot_reply_learning import propose as generate_drafts
        data = await body(request)
        pool, owner, _ = await context(request, data, True)
        return web.json_response({'created': await generate_drafts(pool, owner, int(request.match_info['id']))})

    async def versions(request):
        pool, owner, _ = await context(request)
        rows = await pool.fetch('''SELECT v.version,v.question,v.answer,v.approved_at,v.rejection_reason,
          (v.audio_pcm IS NOT NULL) AS has_audio FROM robot_reply_versions v JOIN robot_reply_candidates c ON c.id=v.candidate_id
          WHERE c.id=$1 AND c.owner=$2 ORDER BY v.version DESC''', int(request.match_info['id']), owner)
        return web.json_response({'items': [dict(r) | {'approved_at': r['approved_at'].isoformat() if r['approved_at'] else None} for r in rows]})

    async def candidate_create(request):
        data = await body(request)
        pool, owner, actor = await context(request, data, True)
        return web.json_response(await lib.create_candidate(pool, owner, int(data.get('project_id') or 0), data.get('question'), data.get('answer'), actor))

    async def remember(request):
        data = await body(request)
        pool, owner, actor = await context(request, data, True)
        return web.json_response(await lib.save_contact_memory(pool, owner, int(request.match_info['id']), data.get('phone'), data.get('callback_at'), actor))

    async def evidence(request):
        import json
        from .robot_reply_learning import evidence_for_candidate
        pool, owner, _ = await context(request)
        items = await evidence_for_candidate(pool, owner, int(request.query.get('project_id') or 0), int(request.match_info['id']))
        for item in items:
            if isinstance(item.get('evidence'), str):
                item['evidence'] = json.loads(item['evidence'])
        return web.json_response({'items': items})

    async def evidence_link(request):
        data = await body(request)
        pool, owner, _ = await context(request, data, True)
        project = int(data.get('project_id') or 0)
        result = await pool.fetchval('''INSERT INTO robot_reply_learning_links(candidate_id,owner,project_id,source_type,source_id,manual)
          SELECT c.id,s.owner,s.project_id,s.source_type,s.source_id,true FROM robot_reply_candidates c
          JOIN robot_reply_learning_sources s ON s.owner=c.owner AND s.project_id=c.project_id
          WHERE c.id=$1 AND c.owner=$2 AND c.project_id=$3 AND s.source_type=$4 AND s.source_id=$5
          ON CONFLICT(candidate_id,owner,project_id,source_type,source_id) DO UPDATE SET manual=true RETURNING 1''', int(request.match_info['id']),owner,project,
          str(data.get('source_type') or ''),str(data.get('source_id') or ''))
        return web.json_response({'linked': bool(result)})

    async def evidence_audio(request):
        pool, owner, _ = await context(request)
        who = await viewer(request)
        project = int(request.query.get('project_id') or 0)
        source_type = request.match_info['source_type']; source_id = request.match_info['source_id']
        exists = await pool.fetchval('''SELECT 1 FROM robot_reply_learning_links l JOIN robot_reply_candidates c
          ON c.id=l.candidate_id AND c.owner=l.owner AND c.project_id=l.project_id
          WHERE c.id=$1 AND c.owner=$2 AND c.project_id=$3 AND l.source_type=$4 AND l.source_id=$5''',
          int(request.match_info['id']),owner,project,source_type,source_id)
        if not exists:
            raise web.HTTPNotFound()
        if source_type == 'robot':
            path = await rs.recording_path(pool, owner, source_id)
            return web.FileResponse(path, headers={'Content-Type':'audio/wav', 'Cache-Control':'no-store'})
        if source_type == 'human' and source_id.isdecimal() and who.actor:
            from . import recording
            data, content_type = await recording.get(pool, who.actor, int(source_id))
            try:
                part = recording.byte_range(request.headers.get('Range'), len(data))
            except ValueError:
                raise web.HTTPRequestRangeNotSatisfiable(headers={'Content-Range': f'bytes */{len(data)}'})
            headers = {'Cache-Control':'no-store', 'Accept-Ranges':'bytes'}
            if part is not None:
                start, end = part
                headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
                return web.Response(body=data[start:end + 1], status=206, content_type=content_type, headers=headers)
            return web.Response(body=data, content_type=content_type, headers=headers)
        raise web.HTTPNotFound()

    async def action(request):
        data = await body(request)
        pool, owner, actor = await context(request, data, True)
        candidate = int(request.match_info['id']); version = int(data.get('version') or 0)
        kind = request.match_info['action']
        if kind == 'edit':
            out = await lib.edit(pool, owner, candidate, version, data.get('question'), data.get('answer'), actor, data.get('context_terms'), data.get('contact_terms'), data.get('triggers'), data.get('next_action', 'reply'), data.get('fact_checked', False))
        elif kind == 'generate':
            out = await lib.generate(pool, owner, candidate, version, actor)
        elif kind == 'preview':
            out = await lib.preview(pool, owner, candidate, version, actor)
        elif kind in ('approve', 'reject'):
            out = await lib.decide(pool, owner, candidate, version, actor, kind, data.get('reason', ''))
        elif kind == 'rollback':
            out = await lib.rollback(pool, owner, candidate, version, int(data.get('target_version') or 0), actor)
        else:
            raise web.HTTPNotFound()
        return web.json_response(out)

    async def audio(request):
        pool, owner, actor = await context(request, write=True)
        raw = await lib.audio(pool, owner, int(request.match_info['id']), int(request.query.get('version') or 0), actor)
        return web.Response(body=raw, content_type='audio/wav', headers={'Cache-Control': 'no-store'})

    base = '/api/robot/cab/replies'
    projects_base = '/api/robot/cab/reply-projects'
    return [web.get(projects_base, project_list), web.post(projects_base, project_create),
            web.post(projects_base + '/{id:\\d{1,18}}/operator', project_operator),
            web.post(projects_base + '/{id:\\d{1,18}}/profile', project_profile),
            web.post(projects_base + '/{id:\\d{1,18}}/contact-memory', remember),
            web.post(projects_base + '/{id:\\d{1,18}}/campaign', campaign_map),
            web.post(projects_base + '/{id:\\d{1,18}}/script', script_map),
            web.get(projects_base + '/{id:\\d{1,18}}/sources', sources),
            web.get(projects_base + '/{id:\\d{1,18}}/metrics', metrics),
            web.post(projects_base + '/{id:\\d{1,18}}/propose', propose),
            web.get(base, listing), web.post(base, candidate_create),
            web.get(base + '/{id:\\d{1,18}}/audio', audio), web.get(base + '/{id:\\d{1,18}}/evidence', evidence),
            web.post(base + '/{id:\\d{1,18}}/evidence', evidence_link),
            web.get(base + '/{id:\\d{1,18}}/evidence/{source_type}/{source_id}', evidence_audio),
            web.get(base + '/{id:\\d{1,18}}/versions', versions),
            web.post(base + '/{id:\\d{1,18}}/{action}', action)]
