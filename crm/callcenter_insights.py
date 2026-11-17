"""Read-only combined view of automatic monitoring and its lead decisions."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import call_analysis, call_analysis_schema, callcenter_base, callcenter_monitor, callcenter_verdict, callcenter_quality

INCEPTION = date(2026, 11, 17)
CATEGORIES = {'qualified', 'reusable', 'competitor', 'pending'}
_WRONG_NUMBER_PROOF = re.compile(
    r'\b(?:не\s+туда\s+попали|(?:вы\s+)?ошиблись\s+(?:номером|телефоном)|'
    r'не\s+тот\s+номер|это\s+не\s+(?:его|е[её]|их)\s+номер)\b', re.I)


def client_wrong_number_evidence(transcript):
    """Return only a short verbatim client phrase when roles are unambiguous."""
    lines = call_analysis.split_lines(transcript)
    operator, clients = callcenter_verdict._roles(lines)
    if not operator or not clients:
        return []
    for line in lines:
        if line.get('speaker') not in clients:
            continue
        text = str(line.get('text') or '')
        if re.search(r'\bне\s+ошиблись\s+(?:номером|телефоном)', text, re.I):
            continue
        found = _WRONG_NUMBER_PROOF.search(text)
        if found:
            return [{'quote': found.group(0), 'at': line.get('at'), 'kind': 'wrong_number'}]
    return []


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _day(value):
    if isinstance(value, datetime):
        return value.astimezone(call_analysis.MOSCOW).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _sheet_index(books):
    index = defaultdict(list)
    for operator, book in (books or {}).items():
        for tab, leads in (book or {}).items():
            for lead in leads:
                phone = callcenter_base._phone_key(lead.get('phone'))
                if phone:
                    entry = {'project': lead.get('project') or tab, 'base': lead.get('base') or '',
                             'tab': tab, 'sheet_id': lead.get('source_sheet_id') or '', 'tag': lead.get('tag') or '', 'dates': {str(a.get('date')) for a in lead.get('attempts', []) if a.get('date') and a.get('result')}}
                    index[(operator, phone)].append(entry)
    return index


def _attribute(index, operator, phone, day):
    entries = index.get((operator, phone), [])
    dated = [e for e in entries if str(day) in e['dates']]
    candidates = dated or entries
    pairs = {(str(e['project']), str(e['base']), str(e['tab']), str(e['sheet_id']), str(e['tag'])) for e in candidates}
    if len(pairs) != 1:
        return {'project': '', 'base': '', 'tab': '', 'attribution': 'ambiguous' if pairs else 'unmatched'}
    project, base, tab, sheet_id, tag = next(iter(pairs))
    return {'project': project, 'base': base, 'tab': tab, 'sheet_id': sheet_id, 'tag': tag, 'attribution': 'matched'}


def _campaign(r):
    return r.get('tab') or 'Не установлено'


def _base_name(r):
    return r.get('project') or 'Не установлено'


def _operator_name(r):
    return {'olga': 'Ольга', 'oksana': 'Оксана'}.get(r.get('operator'), r.get('operator') or 'Не установлено')


async def fetch_records(pool, client_id, start=None, end=None, phone=None, snapshot_data=None):
    """All automatic attempts scoped to a company; only current automatic runs."""
    await call_analysis_schema.ensure(pool)
    has_sources = await pool.fetchval("SELECT to_regclass('callcenter_call_sources') IS NOT NULL")
    source_fields = ",s.sheet_id AS saved_sheet_id,s.tab AS saved_tab,s.project AS saved_project,s.base AS saved_base,s.tag AS saved_tag" if has_sources else ""
    source_join = " LEFT JOIN callcenter_call_sources s ON s.client_id=i.client_id AND s.call_id=i.call_id " if has_sources else ""
    conditions, params = [], [client_id, callcenter_monitor.PREFIX + '%']
    if start is not None and end is not None:
        params.extend([datetime.combine(_day(start), datetime.min.time(), call_analysis.MOSCOW),
                       datetime.combine(_day(end) + timedelta(days=1), datetime.min.time(), call_analysis.MOSCOW)])
        conditions.append('i.at >= $3 AND i.at < $4')
    if phone:
        params.append(phone)
        # Only numbers with a valid Russian prefix are eligible, as in _phone_key.
        conditions.append("(regexp_replace(coalesce(i.phone,''), '[^0-9]', '', 'g') = $%d OR "
                          "regexp_replace(coalesce(i.phone,''), '[^0-9]', '', 'g') IN ('7'||$%d, '8'||$%d))" % ((len(params),) * 3))
    extra = (' AND ' + ' AND '.join(conditions)) if conditions else ''
    rows = await pool.fetch("""SELECT i.*,r.operator,r.id AS analysis_run_id,
        CASE WHEN (i.stages #> '{script_check,signals}') ? 'wrong_number' THEN c.transcript END AS source_transcript""" + source_fields + """
        FROM call_analysis_items i JOIN call_analysis_runs r ON r.id=i.run_id AND r.client_id=i.client_id
        LEFT JOIN calls c ON c.id=i.call_id AND c.client_id=i.client_id
        """ + source_join + """ WHERE r.client_id=$1 AND r.dedup_key LIKE $2 AND r.operator IN ('olga','oksana')
        """ + extra + """ ORDER BY i.at NULLS LAST,i.id""", *params)
    folder = os.getenv('CALLCENTER_LEADS_FOLDER_ID', '').strip()
    if snapshot_data is not None:
        checked_at, books = snapshot_data
    else:
        checked_at, books = (await callcenter_base.snapshot(folder)) if folder and all(c.isascii() and (c.isalnum() or c in '-_') for c in folder) else (None, {})
    index = _sheet_index(books)
    competitor_index = callcenter_base.sheet_competitor_index(books)
    out = []
    # A call can appear in more than one automatic run after configuration changes.
    latest = {}
    for row in rows:
        key = ('call', row['call_id']) if row['call_id'] is not None else ('item', row['id'])
        if key not in latest or row['id'] > latest[key]['id']:
            latest[key] = row
    for row in sorted(latest.values(), key=lambda r: (r['at'].isoformat() if r['at'] else '', r['id'])):
        at = row['at']
        phone = callcenter_base._phone_key(row['phone'])
        stages = _json(row['stages'])
        check = stages.get('script_check') or {}
        if 'wrong_number' in (check.get('signals') or []) and row['source_transcript']:
            check = dict(check, client_evidence=client_wrong_number_evidence(row['source_transcript']))
        rec = {'phone': phone, 'operator': row['operator'], 'at': at.isoformat() if at else None,
               'run_id': row['analysis_run_id'], 'call_id': row['call_id'], 'item_id': row['id'],
               'status': row['status'], 'semantic': stages.get('semantic') or {}, 'duration_s': row['duration_s'] or 0, 'check': check,
               'source': row['transcript_source'], 'error': row['error'], 'sheet_checked_at': checked_at}
        rec.update(_attribute(index, row['operator'], phone, _day(at) if at else ''))
        rec['sheet_competitor'] = (callcenter_base.sheet_competitor(competitor_index, row['operator'], phone, _day(at))
                                   if at and phone and books.get(row['operator']) is not None else None)
        if has_sources and row['saved_sheet_id']:
            rec.update(project=row['saved_project'], base=row['saved_base'], tab=row['saved_tab'],
                       sheet_id=row['saved_sheet_id'], tag=row['saved_tag'], attribution='matched')
        out.append(rec)
    return out


def build_cards(records):
    """Most recent explicit decision for each phone and script; silence does not erase it."""
    history = defaultdict(list)
    decisions = {}
    sheet_markers = {}
    stopped = set()
    for record in sorted(records, key=lambda r: (r.get('at') or '', r.get('item_id') or 0)):
        phone = record.get('phone')
        if not phone:
            continue
        history[phone].append(record)
        marker = record.get('sheet_competitor')
        if marker and marker.get('project'):
            sheet_markers[(phone, marker['project'])] = (marker, record)
        check = record.get('check') or {}
        if check.get('stop_contact') is True:
            stopped.add(phone)
        for decision in check.get('decisions') or []:
            if not isinstance(decision, dict):
                continue
            project, category = decision.get('project'), decision.get('category')
            if project and category in CATEGORIES and (category != 'pending' or (phone, project) not in decisions):
                decisions[(phone, project)] = (decision, record)
    for key, (marker, record) in sheet_markers.items():
        marker_day = _day(datetime.fromisoformat(record['at'])) if record.get('at') else None
        if any(x.get('operator') == record.get('operator') and x.get('at')
               and _day(datetime.fromisoformat(x['at'])) == marker_day
               and (x is record or (x.get('tab') == marker.get('tab') and x.get('sheet_id') == marker.get('sheet_id')))
               and 'wrong_number' in ((x.get('check') or {}).get('signals') or []) for x in history[key[0]]):
            continue
        current = decisions.get(key)
        if current and current[0].get('category') != 'pending':
            continue
        decisions[key] = ({'project': key[1], 'category': 'competitor', 'provenance': 'sheet_operator',
                           'reason': 'Отметка оператора в исходной таблице: ' + marker['marker'],
                           'evidence': [marker], 'next_step': False}, record)
    return [dict(phone=phone, project=project, category=d['category'], reason=d.get('reason') or '',
                 provenance=d.get('provenance') or 'call_analysis',
                 evidence=d.get('evidence') or [], product_qualified=d.get('product_qualified') is True, next_step=d.get('next_step') is True,
                 next_step_text=d.get('next_step_text') or '', at=r['at'], run_id=r['run_id'],
                 call_id=r['call_id'], history_url='#callcenter/:analysis:lead:' + phone,
                 stop_contact=phone in stopped,
                 sources=[dict(sheet_id=x.get('sheet_id'), tab=x.get('tab'), project=x.get('project'), base=x.get('base'), tag=x.get('tag'))
                          for x in history[phone] if x.get('sheet_id')], history=history[phone])
            for (phone, project), (d, r) in sorted(decisions.items())]


async def cards(pool, client_id, snapshot_data=None):
    return build_cards(await fetch_records(pool, client_id, snapshot_data=snapshot_data))


def _metrics(records):
    phones = defaultdict(list)
    for r in records:
        if r.get('phone'):
            phones[r['phone']].append(r)
    reached = {p for p, rs in phones.items() if any((r.get('check') or {}).get('live') for r in rs)}
    decisions = build_cards(records)
    # Product fit can be confirmed even when the customer has not agreed to contact.
    qualified_pairs = {(r['phone'], d.get('project')) for r in records if r.get('phone')
                       for d in (r.get('check') or {}).get('decisions') or []
                       if isinstance(d, dict) and (d.get('product_qualified') is True or d.get('category') == 'qualified')}
    qualified = {phone for phone, _ in qualified_pairs}
    steps = {r['phone'] for r in records if r.get('phone')
             for d in (r.get('check') or {}).get('decisions') or []
             if isinstance(d, dict) and (r['phone'], d.get('project')) in qualified_pairs and d.get('next_step') is True}
    def metric(n, total):
        return {'count': n, 'denominator': total, 'percent': round(100 * n / total, 1) if total else None}
    coverage = {'dialogue': 0, 'missing_or_unclear': 0, 'no_answer_or_machine': 0}
    for record in records:
        check = record.get('check') or {}
        if record.get('status') in ('pending', 'failed', 'missing_call'):
            coverage['missing_or_unclear'] += 1
        elif check.get('live') is True:
            coverage['dialogue'] += 1
        elif check.get('state') in ('not_reached', 'answering_machine'):
            coverage['no_answer_or_machine'] += 1
        else:
            coverage['missing_or_unclear'] += 1
    category_counts = {category: len({d['phone'] for d in decisions if d['category'] == category}) for category in CATEGORIES}
    classified = {d['phone'] for d in decisions if d['category'] in ('qualified', 'reusable', 'competitor')}
    return {'coverage': coverage, 'category_counts': category_counts, 'unclassified_phones': len(set(phones) - classified),
            'attempts': len(records), 'phones': len(phones), 'no_phone': sum(not r.get('phone') for r in records),
            'reached': metric(len(reached), len(phones)), 'qualified': metric(len(qualified & reached), len(reached)),
            'next_step': metric(len(steps & qualified), len(qualified))}


def _breakdown(records, key):
    groups = defaultdict(list)
    for r in records:
        groups[key(r)].append(r)
    return [{'name': name, **_metrics(group)} for name, group in sorted(groups.items())]


def _loss_reasons(records):
    """Conservative observable indicators, not inferred causes or unique losses."""
    groups = defaultdict(list)
    for r in records:
        phone = r.get('phone')
        if not phone:
            continue
        check = r.get('check') or {}
        signals = set(check.get('signals') or [])
        labels = []
        if check.get('state') == 'answering_machine':
            labels.append('Автоответчик')
        elif check.get('state') == 'not_reached':
            labels.append('Нет дозвона')
        if r.get('status') in ('audio_missing', 'transcript_failed'):
            labels.append('Нет записи или расшифровки')
        if 'wrong_number' in signals:
            labels.append('Неверный номер')
        if 'not_relevant' in signals or any(d.get('category') == 'reusable' and d.get('evidence')
                for d in check.get('decisions') or [] if isinstance(d, dict)):
            labels.append('Предложение не подходит или неактуально')
        evidence = [e for d in check.get('decisions') or [] if isinstance(d, dict)
                    for e in d.get('evidence') or [] if isinstance(e, dict)]
        if any(re.search(
                r'перезвон|сейчас (?:некогда|неудобно)', str(e.get('quote') or ''), re.I)
               for e in evidence):
            labels.append('Просьба перенести разговор')
        if check.get('state') == 'review' and check.get('missing'):
            labels.append('Возможный пропуск шага оператора')
        for label in labels:
            groups[label].append({'phone': phone, 'run_id': r['run_id'], 'call_id': r['call_id'],
                                  'at': r.get('at')})
    return groups


def summarize(records, start, end, project="", base="", operator=""):
    start, end = _day(start), _day(end)
    if end < start:
        raise ValueError('Конец периода раньше начала.')
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    def between(r, a, b):
        return bool(r.get('at')) and a <= _day(datetime.fromisoformat(r['at'])) <= b
    in_period = [r for r in records if between(r, prev_start, end)]
    options = {'projects': sorted({_campaign(r) for r in in_period}),
               'bases': sorted({_base_name(r) for r in in_period}),
               'operators': [{'id': k, 'name': _operator_name({'operator': k})}
                             for k in ('olga', 'oksana')]}
    def matches(r):
        return (not project or _campaign(r) == project) and (not base or _base_name(r) == base) and (not operator or r.get('operator') == operator)
    selected = [r for r in in_period if between(r, start, end) and matches(r)]
    previous = [r for r in in_period if between(r, prev_start, prev_end) and matches(r)]
    combinations = defaultdict(list)
    daily_groups = defaultdict(list)
    for r in selected:
        combinations[(_campaign(r), _base_name(r), _operator_name(r))].append(r)
        daily_groups[_day(datetime.fromisoformat(r['at']))].append(r)
    daily = [{'day': (start + timedelta(days=i)).isoformat(),
              **_metrics(daily_groups[start + timedelta(days=i)])} for i in range(days)]
    friction, coaching = defaultdict(list), []
    reasons, previous_reasons = _loss_reasons(selected), _loss_reasons(previous)
    for r in selected:
        check = r.get('check') or {}
        for f in check.get('friction') or []:
            if isinstance(f, dict) and f.get('issue') and f.get('proposal') and f.get('quote') and r.get('phone'):
                code = f.get('issue_code') or 'other'
                key = (f.get('project') or '', f.get('stage_id') or f.get('step') or '', code,
                       f['issue'] if code == 'other' else '')
                friction[key].append({'phone': r['phone'], 'quote': f['quote'], 'at': f.get('at'),
                                      'run_id': r['run_id'], 'call_id': r['call_id'], 'step': f.get('step') or '',
                                      'issue': f['issue'], 'proposal': f['proposal'], 'measure': f.get('measure') or '',
                                      'date': _day(datetime.fromisoformat(r['at'])).isoformat()})
        for c in check.get('coaching') or []:
            if isinstance(c, dict):
                coaching.append({**c, 'phone': r.get('phone'), 'run_id': r['run_id'], 'call_id': r['call_id'],
                                 'project': _campaign(r), 'date': _day(datetime.fromisoformat(r['at'])).isoformat()})
    findings = [{'project': k[0],
                 'project_name': callcenter_quality.SCRIPTS.get(k[0], (k[0] or 'Проект не установлен',))[0],
                 'stage_id': k[1], 'issue_code': k[2],
                 'step': callcenter_verdict.FRICTION_STAGES.get(k[1], v[0]['step']),
                 'issue': callcenter_verdict.FRICTION_ISSUES.get(k[2], 'Другая конкретная проблема'),
                 'specific_issue': k[3] if k[2] == 'other' else '',
                 'leads': len({e['phone'] for e in v}), 'evidence': v}
                for k, v in friction.items()]
    findings.sort(key=lambda f: (-f['leads'], f['project_name'], f['step'], f['issue']))
    recurring = [item for item in findings if item['leads'] >= 2]
    return {'start': start.isoformat(), 'end': end.isoformat(), 'previous_start': prev_start.isoformat(),
            'previous_end': prev_end.isoformat(), 'current': _metrics(selected), 'previous': _metrics(previous), 'daily': daily,
            'projects': _breakdown(selected, _campaign), 'bases': _breakdown(selected, _base_name),
            'operators': _breakdown(selected, _operator_name), 'options': options,
            'filters': {'project': project, 'base': base, 'operator': operator},
            'combinations': [{'project': k[0], 'base': k[1], 'operator': k[2], **_metrics(v)} for k, v in sorted(combinations.items())], 'reasons': [{'reason': k, 'leads': len({e['phone'] for e in reasons.get(k, [])}),
                         'previous': len({e['phone'] for e in previous_reasons.get(k, [])}),
                         'evidence': reasons.get(k, [])} for k in sorted(set(reasons) | set(previous_reasons))],
            'findings': findings, 'friction': recurring, 'coaching': coaching, 'attempts': selected}


async def historical_findings(pool, client_id, project='', base='', operator=''):
    """Read director-pinned call references; conclusions and counts remain derived from source data."""
    if pool is None or not await pool.fetchval("SELECT to_regclass('callcenter_insight_pins') IS NOT NULL"):
        return []
    pins = await pool.fetch("""SELECT call_id,period_start,period_end,script,stage_id,issue_code FROM callcenter_insight_pins
                               WHERE client_id=$1 ORDER BY period_end DESC,call_id""", client_id)
    output, periods = [], {}
    for pin in pins:
        first, last = pin['period_start'], pin['period_end']
        if last < first:
            continue
        period = (first, last)
        if period not in periods:
            records = await fetch_records(pool, client_id, first, last)
            periods[period] = summarize(records, first, last, project, base, operator)
        report = periods[period]
        for finding in report['findings']:
            if (finding['project'], finding['stage_id'], finding['issue_code']) != (pin['script'], pin['stage_id'], pin['issue_code']):
                continue
            examples = [item for item in finding['evidence'] if item.get('call_id') == pin['call_id']]
            if examples:
                output.append({'start': first.isoformat(), 'end': last.isoformat(),
                               'project_name': finding['project_name'], 'step': finding['step'],
                               'issue': finding['issue'], 'leads': finding['leads'], 'evidence': examples})
    return output


async def selected_pins(pool, client_id, report):
    """Only pins still corroborated in the selected report, without rereading old periods."""
    calls = list({item['call_id'] for finding in report['findings'] for item in finding['evidence']
                  if item.get('call_id') is not None})
    if not calls or pool is None or not await pool.fetchval("SELECT to_regclass('callcenter_insight_pins') IS NOT NULL"):
        return []
    pins = await pool.fetch('''SELECT call_id,period_start,period_end,script,stage_id,issue_code
                               FROM callcenter_insight_pins WHERE client_id=$1 AND call_id=ANY($2::bigint[])
                               ORDER BY period_end DESC,call_id''', client_id, calls)
    output = []
    for pin in pins:
        for finding in report['findings']:
            if (finding['project'], finding['stage_id'], finding['issue_code']) != (pin['script'], pin['stage_id'], pin['issue_code']):
                continue
            examples = [item for item in finding['evidence'] if item.get('call_id') == pin['call_id']]
            if examples:
                output.append({'start': pin['period_start'].isoformat(), 'end': pin['period_end'].isoformat(),
                               'project_name': finding['project_name'], 'step': finding['step'],
                               'issue': finding['issue'], 'leads': finding['leads'], 'evidence': examples})
    return output


def report_dates(start=None, end=None):
    today = datetime.now(call_analysis.MOSCOW).date()
    configured = os.getenv('CALLCENTER_MONITOR_FROM', '')
    try:
        inception = _day(datetime.fromisoformat(configured)) if configured else INCEPTION
    except ValueError:
        inception = INCEPTION
    first, last = _day(start or max(inception, today - timedelta(days=6))), _day(end or today)
    if last < first:
        raise ValueError('Конец периода раньше начала.')
    if last > today or (last - first).days > 365:
        raise ValueError('Выберите завершившийся период не более 366 дней.')
    return first, last


async def report_for_company(pool, client_id, start=None, end=None, project="", base="", operator="", _records=None):
    """Build a scoped report without scheduling background work or loading pinned history."""
    first, last = report_dates(start, end)
    if any(len(value) > 200 for value in (project, base, operator)):
        raise ValueError('Некорректный фильтр.')
    previous_start = first - timedelta(days=(last - first).days + 1)
    records = await fetch_records(pool, client_id, previous_start, last) if _records is None else _records
    result = summarize(records, first, last, project, base, operator)
    if operator and operator not in {'olga', 'oksana'}:
        raise ValueError('Неизвестный оператор.')
    if not operator and ((project and project not in result['options']['projects']) or
                         (base and base not in result['options']['bases'])):
        raise ValueError('Выбранный фильтр отсутствует в данных за период.')
    result['advisor_pins'] = await selected_pins(pool, client_id, result)
    return result


async def dashboard(pool, who, start=None, end=None, project="", base="", operator=""):
    if not call_analysis.can_manage(who):
        raise PermissionError('Раздел директора.')
    client_id = call_analysis.company_of(who)
    result = await report_for_company(pool, client_id, start, end, project, base, operator)
    if pool is not None:
        from . import callcenter_advisor_jobs
        try:
            result['advisor'] = await callcenter_advisor_jobs.request(pool, client_id, result)
        except Exception:
            result['advisor'] = {'status': 'error', 'brief': None, 'updated_at': None,
                                 'message': 'Вывод руководителю временно недоступен'}
    result['historical_findings'] = await historical_findings(pool, client_id, project, base, operator)
    result.pop('advisor_pins', None)
    if os.getenv('CALLCENTER_EXPORT_SHEETS'):
        from . import callcenter_export
        try:
            state = await callcenter_export.status(pool, client_id)
        except Exception:
            state = {'status': 'unavailable', 'message': 'Статус выгрузки временно недоступен'}
        result['export_status'] = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in state.items()}
    return result


async def lead(pool, who, phone):
    if not call_analysis.can_manage(who):
        raise PermissionError('Раздел директора.')
    if len(phone) != 10 or not phone.isascii() or not phone.isdigit():
        raise ValueError('Неверный номер.')
    records = [r for r in await fetch_records(pool, call_analysis.company_of(who), phone=phone) if r['phone'] == phone]
    return {'phone': phone, 'attempts': records, 'decisions': build_cards(records),
            'stop_contact': any((r.get('check') or {}).get('stop_contact') is True for r in records)}
