"""HTTP-ручки кабинета робота (/api/robot/cab/...). Подключаются из crm/web.py одной строкой:
app.router.add_routes(robot_api.routes(viewer, body, POOL)).

Директор открывает любой кабинет (?owner=лист клиента, пусто — наш), клиент — только свой
и только если опция включена. Запись разговора отдаём файлом WAV.
"""
from __future__ import annotations

from aiohttp import web

from . import robot_store as rs


def routes(viewer, body, pool_key) -> list:
    async def ctx(request, write=False, data=None):
        who = await viewer(request)
        pool = request.app[pool_key]
        requested = (data or {}).get('owner') if data is not None else request.query.get('owner')
        return pool, who, await rs.owner_for(pool, who, requested, write)

    async def state(request):
        who = await viewer(request)
        pool = request.app[pool_key]
        out = await rs.state(pool, who)
        if out['enabled']:
            out['owner'] = await rs.owner_for(pool, who, request.query.get('owner'))
            out['operators'] = rs.operators(out['owner'])
        if who.role == 'director':
            from voice.results import SHEET_TITLE
            import os
            out['options'] = {'prices': await rs.prices(pool), 'auto': await rs.auto_prices(pool), 'clients': await rs.enabled_clients(pool),
                              'econ': await rs.economics(pool)}
            out['sheet'] = {'on': bool(os.getenv('VOICE_SHEET_ID', '').strip()), 'tab': SHEET_TITLE}
        return web.json_response(out)

    async def options(request):
        who = await viewer(request)
        return web.json_response(await rs.save_options(request.app[pool_key], who, await body(request)))

    async def known_clients(request):
        who = await viewer(request)
        if who.role != 'director':
            return web.json_response({'error': 'Раздел директора.'}, status=403)
        from . import cabinet
        try:
            names = [c['name'] for c in (await cabinet.summary())['clients']]
        except Exception:
            names = []
        return web.json_response({'clients': names})

    # операторы
    async def operator_get(request):
        _, _, owner = await ctx(request)
        return web.json_response(rs.operator(owner, request.match_info['id']))

    async def operator_save(request):
        data = await body(request)
        _, _, owner = await ctx(request, True, data)
        op_id = request.match_info.get('id')
        return web.json_response(rs.save_operator(owner, data, op_id, None if op_id else data.get('copy_from') or None))

    async def operator_delete(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        await rs.delete_operator(pool, owner, request.match_info['id'])
        return web.json_response({'ok': True})

    # контакты
    async def contacts(request):
        pool, _, owner = await ctx(request)
        return web.json_response(await rs.contacts(pool, owner, request.query.get('q', '')))

    async def contacts_upload(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        return web.json_response(await rs.upload_contacts(pool, owner, data))

    async def contacts_tabs(request):
        _, who, _ = await ctx(request)
        try:
            tabs = await rs.sheet_tabs(who)
        except Exception:
            tabs = []
        return web.json_response({'tabs': tabs})

    async def contacts_sheet(request):
        data = await body(request)
        pool, who, owner = await ctx(request, True, data)
        try:
            return web.json_response(await rs.sheet_contacts(pool, who, owner, data.get('tab')))
        except LookupError as exc:
            raise ValueError(str(exc))

    async def contacts_delete(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        return web.json_response({'deleted': await rs.delete_contacts(pool, owner, data.get('ids'))})

    # стоп-лист
    async def stop(request):
        pool, _, owner = await ctx(request)
        return web.json_response({'items': await rs.stoplist(pool, owner)})

    async def stop_add(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        return web.json_response({'added': await rs.stop_add(pool, owner, data.get('phones'), data.get('reason'))})

    async def stop_remove(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        await rs.stop_remove(pool, owner, data.get('phone'))
        return web.json_response({'ok': True})

    # кампании
    async def campaigns(request):
        pool, _, owner = await ctx(request)
        return web.json_response({'items': await rs.campaigns(pool, owner)})

    async def campaign_save(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        cid = request.match_info.get('id')
        return web.json_response(await rs.save_campaign(pool, owner, data, int(cid) if cid else None))

    async def campaign_action(request):
        data = await body(request)
        pool, _, owner = await ctx(request, True, data)
        return web.json_response(await rs.campaign_action(pool, owner, int(request.match_info['id']), request.match_info['action']))

    async def campaign_targets(request):
        pool, _, owner = await ctx(request)
        return web.json_response({'items': await rs.targets(pool, owner, int(request.match_info['id']))})

    # звонки и аналитика
    async def calls(request):
        pool, who, owner = await ctx(request)
        return web.json_response(await rs.calls(pool, who, owner, dict(request.query)))

    async def calls_csv(request):
        pool, who, owner = await ctx(request)
        items = (await rs.calls(pool, who, owner, dict(request.query), limit=20000))['items']
        return web.Response(text=rs.calls_csv(items), content_type='text/csv', charset='utf-8',
                            headers={'Content-Disposition': 'attachment; filename="robot-calls.csv"'})

    async def recording(request):
        pool, _, owner = await ctx(request)
        try:
            path = await rs.recording_path(pool, owner, request.match_info['id'])
        except LookupError as exc:
            return web.json_response({'error': str(exc)}, status=404)
        return web.FileResponse(path, headers={'Content-Type': 'audio/wav'})

    async def analytics(request):
        pool, who, owner = await ctx(request)
        return web.json_response(await rs.analytics(pool, who, owner, dict(request.query)))

    base = '/api/robot/cab'
    return [
        web.get(base, state), web.post(base + '/options', options), web.get(base + '/known-clients', known_clients),
        web.post(base + '/operators', operator_save), web.get(base + '/operators/{id}', operator_get),
        web.post(base + '/operators/{id}', operator_save), web.post(base + '/operators/{id}/delete', operator_delete),
        web.get(base + '/contacts', contacts), web.post(base + '/contacts/upload', contacts_upload),
        web.get(base + '/contacts/tabs', contacts_tabs), web.post(base + '/contacts/sheet', contacts_sheet),
        web.post(base + '/contacts/delete', contacts_delete),
        web.get(base + '/stop', stop), web.post(base + '/stop', stop_add), web.post(base + '/stop/remove', stop_remove),
        web.get(base + '/campaigns', campaigns), web.post(base + '/campaigns', campaign_save),
        web.post(base + '/campaigns/{id:\\d+}', campaign_save), web.get(base + '/campaigns/{id:\\d+}/targets', campaign_targets),
        web.post(base + '/campaigns/{id:\\d+}/{action}', campaign_action),
        web.get(base + '/calls', calls), web.get(base + '/calls.csv', calls_csv), web.get(base + '/recording/{id}', recording),
        web.get(base + '/analytics', analytics),
    ]
