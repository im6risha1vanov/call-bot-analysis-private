"""Export finalized automatic Teams meeting snapshots to the local CRM."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

BASE = Path('/var/lib/averon-meetings')
CHAT_ID = '19:IlPIPusKLobZc23AinyG0v2-cdtG311KTVyRLVUMIOg1@thread.v2'
FINAL = {'complete', 'capture_failed', 'transcription_partial', 'transcription_failed', 'failed', 'interrupted'}
SESSION = re.compile(r'\d{8}T\d{6}Z-[a-f0-9]{8}\Z')
EVENT = re.compile(r'\d{13}\Z')


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def read(path, optional=False):
    try:
        if path.is_symlink():
            raise ValueError('symlink refused')
        before = path.stat()
        if before.st_size > 20_000_000:
            raise ValueError('oversized file')
        value = json.loads(path.read_text(encoding='utf-8'))
        after = path.stat()
        if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino):
            raise ValueError('file changed during read')
        return value, after.st_mtime_ns
    except FileNotFoundError:
        if optional:
            return None, 0
        raise


def parse_time(s):
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt.timestamp()


def snapshot(base, session_id, ledger, cutoff):
    if not SESSION.fullmatch(session_id):
        return None
    directory = base / 'sessions' / session_id
    if directory.is_symlink():
        return None
    state, state_ns = read(directory / 'status.json')
    event_id = state.get('teams_event_id', '')
    if (state.get('id') != session_id or state.get('mode') != 'meeting' or
        state.get('state') not in FINAL or not EVENT.fullmatch(str(event_id)) or
        parse_time(state['created_at']) < cutoff):
        return None
    entry = ledger.get(event_id, {})
    if entry.get('state') != 'started' or entry.get('session_id') != session_id:
        return None
    event, _ = read(base / 'teams' / 'events' / (event_id + '.json'))
    if event.get('chat_id') != CHAT_ID or event.get('message_id') != event_id:
        return None
    raw, transcript_ns = read(directory / 'transcript.json', optional=True)
    transcript = copy.deepcopy(raw)
    ids = []
    if transcript is not None:
        if transcript.get('session_id') != session_id or not isinstance(transcript.get('utterances'), list):
            raise ValueError('invalid transcript')
        for u in transcript['utterances']:
            identity = {k: u.get(k) for k in ('source', 'start', 'end', 'speaker', 'text')}
            u['id'] = 'u-' + digest(identity)
            ids.append(u['id'])
    report_path = base / 'notifications' / session_id / 'report.json'
    report, report_ns = read(report_path, optional=True)
    # The report writer uses this exact serialization for its analysis cache.
    # A report older than a changed transcript is never considered current.
    if report is not None:
        transcript_digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if raw else ''
        cache = base / 'notifications' / session_id / ('analysis-' + transcript_digest[:16])
        if (not raw or (not cache.is_dir() and not report.get('analysis_error')) or report_ns < transcript_ns or
                report.get('session_id') != session_id):
            report, report_ns = None, 0
        else:
            report = copy.deepcopy(report)
            for rows in report.get('notes', {}).values():
                for row in rows:
                    refs = row.get('evidence_ids', [])
                    if any(type(i) is not int or i < 0 or i >= len(ids) for i in refs):
                        raise ValueError('invalid report evidence')
                    row['evidence_ids'] = [ids[i] for i in refs]
    safe_keys = ('id', 'state', 'created_at', 'recording_started_at', 'recording_ended_at',
                 'finished_at', 'title', 'capture_warning', 'capture_error')
    safe_state = {k: state[k] for k in safe_keys if k in state}
    updated = max(state_ns, transcript_ns, report_ns)
    source_updated_at = datetime.fromtimestamp(updated / 1e9, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    payload = {'schema_version': 1, 'session_id': session_id, 'source': 'teams_auto',
               'source_updated_at': source_updated_at, 'state': safe_state, 'transcript': transcript, 'report': report}
    payload['revision'] = digest(payload)
    return payload


def fit_payload(payload, limit=390_000):
    """Keep every report reference, then other utterances in original order."""
    result = copy.deepcopy(payload)
    if len(canonical(result)) <= limit:
        return result
    transcript = result.get('transcript')
    if transcript:
        all_rows = transcript['utterances']
        refs = {ref for rows in (result.get('report') or {}).get('notes', {}).values()
                for row in rows for ref in row.get('evidence_ids', [])}
        selected = [u for u in all_rows if u['id'] in refs]
        transcript['utterances'] = selected
        transcript['export_truncated'] = True
        transcript['original_utterance_count'] = len(all_rows)
        transcript['excluded_utterance_count'] = len(all_rows) - len(selected)
        result['export_warning'] = 'transcript_truncated'
        result['state']['capture_warning'] = (result['state'].get('capture_warning', '') + ' CRM: расшифровка сокращена из-за ограничения размера; полный оригинал сохранён у регистратора.').strip()
        result['revision'] = digest({k: v for k, v in result.items() if k != 'revision'})
        if len(canonical(result)) <= limit:
            selected_ids = {u['id'] for u in selected}
            for u in all_rows:
                if u['id'] in selected_ids:
                    continue
                transcript['utterances'].append(u)
                transcript['excluded_utterance_count'] -= 1
                if len(canonical(result)) > limit - 128:
                    transcript['utterances'].pop()
                    transcript['excluded_utterance_count'] += 1
                    break
            order = {u['id']: i for i, u in enumerate(all_rows)}
            transcript['utterances'].sort(key=lambda u: order[u['id']])
            result['revision'] = digest({k: v for k, v in result.items() if k != 'revision'})
            return result
    result['transcript'] = None
    result['report'] = None
    result['export_warning'] = 'oversize_state_only'
    result['state']['capture_warning'] = (result['state'].get('capture_warning', '') + ' CRM: текст и отчёт не импортированы из-за ограничения размера; оригиналы сохранены у регистратора.').strip()
    result['revision'] = digest({k: v for k, v in result.items() if k != 'revision'})
    if len(canonical(result)) > limit:
        raise ValueError('state exceeds import limit')
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect refused')


def send(url, token, payload):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or
        parsed.path != '/api/internal/team/meetings/import' or parsed.query or parsed.fragment or parsed.username):
        raise ValueError('CRM URL must be the loopback import endpoint')
    request = Request(url, data=canonical(payload), method='POST', headers={'Content-Type': 'application/json',
                      'Authorization': 'Bearer ' + token, 'X-CRM': '1'})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=10) as response:
                if 200 <= response.status < 300:
                    return
                raise ValueError('unexpected response')
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def ledger_path(base=BASE):
    credentials = os.environ.get('CREDENTIALS_DIRECTORY')
    return Path(credentials) / 'meeting_dispatch' if credentials else base / 'teams' / 'dispatch-ledger.json'


def main():
    import fcntl
    os.umask(0o077)
    cutoff = parse_time(os.environ['MEETINGS_IMPORT_NOT_BEFORE'])
    url = os.environ['CRM_MEETINGS_IMPORT_URL']
    token = os.environ['CRM_MEETINGS_IMPORT_TOKEN']
    if not token or '\n' in token or '\r' in token:
        raise ValueError('invalid token')
    root = BASE / 'crm-import'
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger, _ = read(ledger_path(BASE))
        tracking, _ = read(root / 'imported.json', optional=True)
        tracking = tracking or {}
        failed = False
        for folder in sorted((BASE / 'sessions').iterdir()):
            try:
                payload = snapshot(BASE, folder.name, ledger, cutoff)
                if payload is None or tracking.get(folder.name, {}).get('revision') == payload['revision']:
                    continue
                source_revision = payload['revision']
                outgoing = fit_payload(payload)
                send(url, token, outgoing)
                tracking[folder.name] = {'revision': source_revision, 'state': outgoing.get('export_warning', 'imported')}
                atomic_json(root / 'imported.json', tracking)
                print('imported', folder.name, flush=True)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                failed = True
                print('import failed', folder.name, type(exc).__name__, file=sys.stderr, flush=True)
        return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
