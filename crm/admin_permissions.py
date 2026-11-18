"""Restrictions for existing CRM roles. Existing record-level checks still apply."""
from tools import Forbidden


SECTIONS = {
    'deals': ('/api/deals', '/api/tgchats', '/api/presentations'),
    'leads': ('/api/leads', '/api/queue', '/api/tasks', '/api/callbacks', '/api/card', '/api/outcome'),
    'calls': ('/api/calls', '/api/recording', '/api/review', '/api/dial', '/api/webphone', '/webphone/ws'),
    'reports': ('/api/reports', '/api/stats', '/api/summary', '/api/economics', '/api/journal'),
    'staff': ('/api/staff', '/api/access', '/api/person', '/api/activity'),
    'callcenter': ('/api/callcenter', '/api/cabinet'),
    'robot': ('/api/robot',),
    'contracts': ('/api/contracts',),
    'sources': ('/api/sources', '/api/donors'),
    'gpt': ('/api/gpt', '/api/ask', '/api/prompts'),
    'automations': ('/api/automations',),
}
ACTIONS = ('view', 'edit', 'export', 'run')


def contains(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


def permission_for(method, path):
    section = next((name for name, routes in SECTIONS.items()
                    if any(contains(path, prefix) for prefix in routes)), None)
    if section is None:
        return None
    parts = path.split('/')
    if 'export' in parts or path.endswith('.csv'):
        action = 'export'
    elif path == '/webphone/ws':
        action = 'run'
    elif method in {'GET', 'HEAD'}:
        action = 'view'
    elif (parts[-1] in {'start', 'resume', 'enable', 'test', 'run', 'call', 'telegram', 'dial'}
          or path in {'/api/callcenter/analysis', '/api/donors', '/api/ask', '/api/gpt'}):
        action = 'run'
    else:
        action = 'edit'
    return section, action


async def denied(pool, client_id, role):
    if not await pool.fetchval("SELECT to_regclass('crm_admin_role_rules') IS NOT NULL"):
        return set()
    value = await pool.fetchval(
        'SELECT denied FROM crm_admin_role_rules WHERE client_id=$1 AND role=$2', client_id, role)
    return set(value or ())


def restrictions(section, action):
    return {f'{section}.view', f'{section}.{action}'}


async def enforce(pool, who, permission):
    if permission and who and who.company is not None:
        if await denied(pool, who.company, who.role) & restrictions(*permission):
            raise Forbidden('Действие отключено для роли в настройках компании.')
