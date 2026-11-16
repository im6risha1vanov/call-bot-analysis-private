"""Owner-scoped HTTP endpoints for personal agent jobs."""
import json
import re
from aiohttp import web
from crm import personal
from tools import Forbidden
import personal_executor as engine


def routes(viewer, body, pool_key):
    async def context(request):
        pool=request.app[pool_key]; who=await viewer(request)
        if await personal.identity(pool,who) is None: raise Forbidden('Личный раздел недоступен.')
        return pool,who.actor
    async def jobs(request):
        pool,actor=await context(request)
        if request.method=='POST':
            data=await body(request)
            if set(data)!={'request','request_id'} or not isinstance(data['request_id'],str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}',data['request_id']): raise ValueError('Укажите поручение.')
            row=await engine.submit(pool,actor,data['request'],'crm:'+str(actor.telegram_user_id)+':'+data['request_id'])
            return web.json_response({'job': personal.serial(row) if row else None},status=201 if row else 200)
        rows=await pool.fetch('SELECT * FROM crm_personal_agent_jobs WHERE telegram_user_id=$1 AND client_id=$2 ORDER BY id DESC LIMIT 30',actor.telegram_user_id,actor.client_id)
        return web.json_response({'jobs':[personal.serial(r) for r in rows]})
    async def detail(request):
        pool,actor=await context(request)
        try: job_id=int(request.match_info['id'])
        except ValueError: raise web.HTTPNotFound()
        job=await pool.fetchrow('SELECT * FROM crm_personal_agent_jobs WHERE id=$1 AND telegram_user_id=$2 AND client_id=$3',job_id,actor.telegram_user_id,actor.client_id)
        if not job: raise web.HTTPNotFound()
        steps=await pool.fetch('SELECT sequence,tool,status,evidence,created_at FROM crm_personal_agent_steps WHERE job_id=$1 ORDER BY sequence',job_id)
        action=await pool.fetchrow('SELECT token,recipient,recipient_name,sender_name,content,status,message_id FROM crm_personal_agent_actions WHERE job_id=$1',job_id)
        return web.json_response({'job':personal.serial(job),'steps':[personal.serial(x) for x in steps], 'action':personal.serial(action) if action else None})
    async def confirm(request):
        pool,actor=await context(request); data=await body(request)
        if set(data)!={'token','approve'} or type(data['approve']) is not bool or not isinstance(data['token'],str): raise ValueError('Неверное подтверждение.')
        try: job_id=int(request.match_info['id'])
        except ValueError: raise web.HTTPNotFound()
        status=await engine.approve(pool,actor,job_id,data['token'],data['approve'])
        return web.json_response({'status':status})
    return [web.get('/api/personal/agent/jobs',jobs),web.post('/api/personal/agent/jobs',jobs),web.get('/api/personal/agent/jobs/{id}',detail),web.post('/api/personal/agent/jobs/{id}/confirm',confirm)]
