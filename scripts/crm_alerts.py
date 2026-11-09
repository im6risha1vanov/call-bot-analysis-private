"""Send sanitized operational alerts from systemd to a configured private chat."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

UNITS = {
    'callbot-crm.service': 'CRM',
    'callbot-astra.service': 'Telegram-бот',
    'callbot-astra-worker.service': 'Фоновая обработка',
    'callbot-astra-queue-runner.service': 'Очередь задач',
    'callbot-astra-trainer.service': 'Тренажёр',
}
ISSUES = {
    **{f'log:{unit}': f'{label} — ошибки в системном журнале' for unit, label in UNITS.items()},
    **{f'down:{unit}': f'{label} — служба была недоступна' for unit, label in UNITS.items()},
    'http': 'CRM — локальный API не отвечал штатно',
    'journal': 'Мониторинг — ошибка чтения системного журнала',
    'journal_limit': 'Мониторинг — за один проход обработана только часть журнала',
    'systemd': 'Мониторинг — ошибка проверки служб systemd',
}
ERROR_LINE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[,.]\d+)? (?:ERROR|CRITICAL)\b')
JOURNAL_LIMIT = 3000
JOURNAL_TIMEOUT = 15  # оставляем время на остальные проверки и отправку в пределах 35 секунд systemd
COOLDOWN = 15 * 60
RETRY_DELAY = 5 * 60


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = build_opener(ProxyHandler({}), NoRedirect())


def read_journal(start: int, end: int) -> Counter:
    units = [arg for unit in UNITS for arg in ('--unit', unit)]
    result = subprocess.run(
        ['journalctl', '--no-pager', '--quiet', '--output=json',
         '--output-fields=_SYSTEMD_UNIT,UNIT,PRIORITY,MESSAGE', f'--since=@{start}', f'--until=@{end}',
         f'--lines={JOURNAL_LIMIT}', *units],
        check=True, capture_output=True, text=True, encoding='utf-8', timeout=JOURNAL_TIMEOUT,
    )
    counts = Counter()
    lines = result.stdout.splitlines()
    if len(lines) >= JOURNAL_LIMIT:
        counts['journal_limit'] = 1
    for line in lines:
        row = json.loads(line)
        if int(row['__REALTIME_TIMESTAMP']) <= start * 1_000_000:
            continue
        unit = row.get('UNIT') if row.get('_SYSTEMD_UNIT') == 'init.scope' else row.get('_SYSTEMD_UNIT')
        if not isinstance(unit, str) or unit not in UNITS:
            continue
        message, priority = row.get('MESSAGE'), row.get('PRIORITY')
        if priority in ('0', '1', '2', '3') or isinstance(message, str) and ERROR_LINE.match(message):
            counts[f'log:{unit}'] += 1
    return counts


def inactive_units() -> set[str]:
    result = subprocess.run(
        ['systemctl', 'show', '--property=Id,LoadState,ActiveState', *UNITS],
        capture_output=True, text=True, encoding='utf-8', timeout=5,
    )
    if not result.stdout.strip():
        raise RuntimeError('Cannot inspect systemd units')
    active = set()
    for block in result.stdout.strip().split('\n\n'):
        props = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if props.get('LoadState') == 'loaded' and props.get('ActiveState') == 'active':
            active.add(props.get('Id'))
    return {f'down:{unit}' for unit in UNITS.keys() - active}


def api_ready(port: int) -> bool:
    try:
        with HTTP.open(f'http://127.0.0.1:{port}/api/me', timeout=3):
            return False
    except HTTPError as exc:
        with exc:
            return exc.code == 401 and exc.headers.get_content_type() == 'application/json'
    except (OSError, URLError):
        return False


def send(token: str, chat: int, message: str) -> bool:
    req = Request(f'https://api.telegram.org/bot{token}/sendMessage',
                  data=json.dumps({'chat_id': chat, 'text': message}).encode(),
                  headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with HTTP.open(req, timeout=8) as response:
            body = json.loads(response.read(32768))
            return isinstance(body, dict) and body.get('ok') is True
    except (OSError, ValueError):
        # Do not print exception objects: an HTTP error may contain the bot token.
        return False


def poll(state: dict, now: int, port: int) -> None:
    start = min(state.get('checked_until', now - 60), now)
    start = max(start, now - 24 * 60 * 60)
    pending = Counter(state.get('pending', {}))
    if start < now:
        try:
            pending.update(read_journal(start, now))
            state['checked_until'] = now
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            print(f'CRM journal check failed ({type(error).__name__}).', file=sys.stderr)
            pending['journal'] += 1
    try:
        unhealthy = inactive_units()
    except (OSError, subprocess.SubprocessError, RuntimeError):
        pending['systemd'] += 1
        unhealthy = {key for key in state.get('unhealthy', []) if key.startswith('down:')}
    if not api_ready(port):
        unhealthy.add('http')
    pending.update(unhealthy.intersection(state.get('unhealthy', [])))
    state['unhealthy'] = sorted(unhealthy)
    state['pending'] = dict(pending)


def deliver(state: dict, now: int, token: str, chat: int) -> bool:
    last_sent = state.setdefault('last_sent', {})
    pending = state['pending']
    due = [key for key in ISSUES if pending.get(key) and now >= last_sent.get(key, -COOLDOWN) + COOLDOWN]
    if not due or now < state.get('retry_at', 0):
        return True
    text = 'Averon CRM. Обнаружены технические сбои.\n\n' + '\n'.join(
        f'• {ISSUES[key]} ({pending[key]})' for key in due
    )
    if not send(token, chat, text):
        state['retry_at'] = now + RETRY_DELAY
        return False
    state.pop('retry_at', None)
    for key in due:
        last_sent[key] = now
        del pending[key]
    return True


def save_state(path: Path, state: dict) -> None:
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state), encoding='utf-8')
    temp.replace(path)


def main() -> int:
    token = os.getenv('BOT_TOKEN', '')
    recipient = os.getenv('CRM_ALERT_CHAT_ID', '').strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) or not re.fullmatch(r'[1-9][0-9]{0,15}', recipient):
        print('CRM alerts require BOT_TOKEN and a positive CRM_ALERT_CHAT_ID.', file=sys.stderr)
        return 2
    state_path = Path(os.environ['STATE_DIRECTORY']) / 'state.json'
    port = int(os.getenv('CRM_ALERT_HTTP_PORT') or os.getenv('CRM_PORT') or '8095')
    if not 1 <= port <= 65535:
        raise ValueError('Invalid CRM health port')
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {}
    now = int(time.time())
    poll(state, now, port)
    save_state(state_path, state)
    delivered = deliver(state, now, token, int(recipient))
    save_state(state_path, state)
    if not delivered:
        print('Telegram alert delivery failed. Pending alerts will be retried.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('CRM alert monitor failed. Check its configuration and local state.', file=sys.stderr)
        raise SystemExit(2) from None
