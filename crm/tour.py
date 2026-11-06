"""Normal-account CRM orientation. No copies, progress or operational writes."""
from __future__ import annotations
import json,re
from pathlib import Path
from aiohttp import web
COOKIE='crm_learning_mode'
MESSAGE='Включён режим обучения: действие объясняется без звонка, отправки или сохранения. Чтобы работать с данными, выключите режим обучения.'
# Only existing passive reads. Unknown future endpoints fail closed while learning.
READS={
 '/api/me','/api/learning/guide','/api/learning/context','/api/summary','/api/journal','/api/callbacks','/api/queue','/api/card','/api/tasks','/api/calls','/api/stats',
 '/api/reports','/api/person','/api/staff','/api/access','/api/trainings','/api/ask','/api/gpt','/api/docs',
 '/api/deals','/api/contracts','/api/contracts/config','/api/contracts/next-number',
 '/api/prompts','/api/leads','/api/sources/selections','/api/donors','/api/callcenter','/api/callcenter/work','/api/automations',
 '/api/robot','/api/robot/calls','/api/robot/cab','/api/robot/cab/known-clients','/api/robot/cab/contacts','/api/robot/cab/contacts/tabs','/api/robot/cab/stop','/api/robot/cab/campaigns','/api/robot/cab/calls','/api/robot/cab/analytics',
}
READ_PATTERNS=[re.compile(p) for p in (
 r'/api/deals/[^/]+',r'/api/review/[^/]+',r'/api/recording/[^/]+',r'/api/trainings/[^/]+',r'/api/tgchats/[^/]+',
 r'/api/sources/selections/[0-9]+',r'/api/donors/[0-9]+',r'/api/automations/[0-9]+/runs',
 r'/api/prompts/(?:version/[0-9]+|[a-z_]+)',r'/api/robot/cab/operators/[0-9]+',r'/api/robot/cab/campaigns/[0-9]+/targets',r'/api/robot/cab/recording/[^/]+',
)]
AUTH_POSTS={'/api/learning/mode','/api/login','/api/logout','/api/test/login'}
def active(request):return bool(request.cookies.get(COOKIE))
def permitted(method,path):
 if method in {'POST'} and path in AUTH_POSTS:return True
 if method not in {'GET','HEAD'}:return False
 if path in {'/','/logout','/learning/enter','/learning/exit','/learning/workspace','/learning/practice'} or path.startswith('/static/'):return True
 return path in READS or any(p.fullmatch(path) for p in READ_PATTERNS)
def context(who,firm):
 if who.role not in {'manager','director'} or who.actor is None or who.company!=who.actor.client_id or firm.get('id',who.company)!=who.company:
  raise web.HTTPForbidden(reason='Обучение доступно менеджеру и директору своей компании.')
 modules=firm.get('modules') or []
 data=json.loads((Path(__file__).parent/'learning_guide.json').read_text())
 return {'role':who.role,'tabs':[t for t in data[who.role] if not t.get('module') or t['module'] in modules],
         'examples':json.loads((Path(__file__).parent/'learning_examples.json').read_text()),'progress':'not_stored','records':'normal_authorized_reads'}
