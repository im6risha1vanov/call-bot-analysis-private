"""The only ordinary automatic call-report delivery path."""
import json
from queue_runner import register, on_startup
from unified import store


@on_startup
async def recover_report_delivery(pool):
    # Unknown Bot API outcome cannot be automatically resent after a restart.
    await pool.execute("UPDATE call_report_delivery SET status='uncertain',error='interrupted Telegram send',updated_at=now() WHERE status='sending'")


@register('publish_call_report')
async def publish_call_report(pool, task):
    from handlers.analyze_call import _bot
    data = json.loads(task['input']) if isinstance(task['input'], str) else task['input']
    if not isinstance(data, dict) or type(data.get('run_id')) is not int:
        raise ValueError('Invalid report task')
    client_id = await pool.fetchval('SELECT client_id FROM call_report_runs WHERE id=$1', data['run_id'])
    if client_id != task['client_id']:
        raise ValueError('Report task client mismatch')
    row = await store.prepare(pool, data['run_id'])
    delivery = await store.deliver(pool, _bot, row)
    return {'run_id': row['id'], 'call_id': row['call_id'], 'delivery': delivery}
