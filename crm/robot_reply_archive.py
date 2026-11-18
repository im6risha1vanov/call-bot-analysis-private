"""Historical project-source expansion. Reads calls; never changes telephony or source transcripts.

Only the already-confirmed packaging project mapping and operator extensions are eligible.
A linked sheet is stronger attribution than a keyword. Unlinked calls require the
operator's clear packaging introduction, not a product word from a customer's reply.
"""
from __future__ import annotations
import re
import hashlib
import json
from collections import Counter
from . import call_analysis, callcenter_quality, robot_reply_learning

SHEET = 'Картонная упаковка'
INTRO = re.compile(r'(?:производств\w*|производим|производител\w*).{0,45}(?:картонн\w*\s+упаковк\w*|короб\w*|гофрокартон\w*)|(?:картонн\w*\s+упаковк\w*|гофрокартон\w*).{0,45}(?:производств\w*|производим|производител\w*)', re.I)
SELF_INTRO = re.compile(r'(?:меня зовут|это|я)\s+(?:ольга|оксана)\b|вас беспокоит|\bзвоню\b|\bзвоним\b|представляю\s+(?:производств|компани)', re.I)
OUTBOUND_QUESTION = re.compile(r'(?:подскажите|скажите|расскажите|уточните).{0,65}(?:вам|вас|вы|ваш)', re.I)
CLIENT_PRODUCTION = re.compile(r'\bвы\s+(?:сами\s+)?(?:производите|производитель|производство)\b', re.I)
OTHER_INTRO = re.compile(r'(?:звон\w*|предлага\w*|занимаемся|предоставляем).{0,50}(?:бухгалтерск\w*|подбор\w* сотрудников|(?:строительств\w*|производств\w*) ангар\w*|кухонн\w* гарнитур\w*)', re.I)
FILLER = re.compile(r'^(?:алло|слушаю(?: вас)?|здравствуйте|добрый (?:день|вечер)|да|нет|угу|ага|хорошо|спасибо|понятно)[.!? ]*$', re.I)
MACHINE = re.compile(r'оставьте (?:голосовое )?сообщение|говорите после сигнала|абонент недоступен|абонент не отвечает|голосов(?:ая почта|ой почтовый ящик|ой помощник абонента)|для соединения нажмите|виртуальн\w* помощник (?:абонента|обонента)|ваш звонок очень важен для нас', re.I)


def resolve_operators(specs, olga_override=None):
    found = {str(s['id']): str(s['extension']) for s in specs if s.get('configured') and s.get('extension')}
    if olga_override:
        proposed = str(olga_override).strip()
        if not proposed.isdigit(): raise ValueError('Добавочный Ольги должен состоять из цифр')
        if found.get('olga') and found['olga'] != proposed:
            raise ValueError('Добавочный Ольги противоречит подтверждённым настройкам CRM')
        found['olga'] = proposed
    selected = {key:found[key] for key in ('olga','oksana','robot') if key in found}
    if len(set(selected.values())) != len(selected):
        raise ValueError('Добавочные операторов пересекаются; импорт остановлен')
    return selected


def outbound_intro(text):
    """Self-identification or a clear caller's product introduction, not client description."""
    text = str(text or '')
    if SELF_INTRO.search(text):
        return True
    return bool(INTRO.search(text) and OUTBOUND_QUESTION.search(text) and not CLIENT_PRODUCTION.search(text))


def role_map(lines):
    labels = {line.get('speaker') for line in lines if line.get('speaker')}
    explicit_ops = {label for label in labels if re.match(r'^(?:оператор|менеджер|operator)\b', str(label), re.I)}
    explicit_clients = {label for label in labels if re.match(r'^(?:клиент|абонент|client)\b', str(label), re.I)}
    if len(labels)==2 and len(explicit_ops)==1:
        operator=next(iter(explicit_ops))
        return operator, labels-{operator}, 'explicit_labels'
    if len(labels)!=2:
        return None, None, 'ambiguous'
    # Speaker numbering is not a role. Do not use a product mention alone: the
    # customer may ask about the producer or describe their own manufacturing.
    intros = {line.get('speaker') for line in lines
              if line.get('speaker') and outbound_intro(line.get('text'))}
    if len(intros)==1:
        operator=next(iter(intros))
        return operator, labels-{operator}, 'inferred_intro'
    return None, None, 'ambiguous'


def substantive(item):
    text = str(item.get('question') or '').strip()
    if not text or FILLER.fullmatch(text) or MACHINE.search(text) or callcenter_quality.DECLINE.search(text) or callcenter_quality.WRONG.search(text):
        return False
    return (len(robot_reply_learning.normal(text).split()) >= 2 or (text.endswith('?') and len(text) >= 5))


def materialize(raw):
    lines=call_analysis.split_lines(raw)
    operator,clients,method=role_map(lines)
    if not operator or not clients:
        return 'unattributed','speaker_roles_unavailable',[],method
    # Preserve each original timestamp; labels are assigned only from validated roles.
    terminal = next((i for i,line in enumerate(lines) if line.get('speaker') in clients and
                     (callcenter_quality.DECLINE.search(str(line.get('text') or '')) or callcenter_quality.WRONG.search(str(line.get('text') or '')))), len(lines))
    lines = lines[:terminal]
    roles=[{'role':'operator' if line.get('speaker') == operator else 'client' if line.get('speaker') in clients else '',
            'at':line.get('at'),'text':line.get('text')} for line in lines]
    status,reason,evidence=robot_reply_learning.extract(roles)
    evidence = [item for item in evidence if substantive(item)]
    if status == 'extracted' and not evidence:
        status, reason = 'no_pairs', 'no_substantive_client_turns'
    if method in ('inferred_intro', 'inferred_packaging_intro'):
        for item in evidence:
            item['labels'] = [x for x in item['labels'] if x != 'explicit'] + ['inferred_intro']
    return status,reason,evidence,method


def classify(row, operators):
    """Return (classification, source_kind, operator) without exposing call text."""
    ext = str(row.get('extension') or '')
    who = next((name for name in ('olga','oksana') if operators.get(name) == ext), None)
    if who is None:
        return ('robot_or_unverified_operator',None,None)
    linked = row.get('tab') == SHEET
    if row.get('tab') and not linked:
        return ('other_verified_sheet',None,who)
    raw = str(row.get('transcript') or '').strip()
    if not raw:
        return ('missing_transcript',None,who)
    if int(row.get('duration_seconds') or 0) < 10 or len(raw) < 70:
        return ('too_short',None,who)
    lines = call_analysis.split_lines(raw)
    operator, clients, method = role_map(lines)
    if not operator or not clients:
        if any(MACHINE.search(str(line.get('text') or '')) for line in lines):
            return ('answering_machine',None,who)
        return ('ambiguous_speakers',None,who)
    client_lines = [line for line in lines if line.get('speaker') in clients]
    operator_lines = [line for line in lines if line.get('speaker') == operator]
    if any(MACHINE.search(str(line.get('text') or '')) for line in client_lines):
        return ('answering_machine',None,who)
    op_text = ' '.join(str(line.get('text') or '') for line in operator_lines)
    has_intro = bool(INTRO.search(op_text))
    if OTHER_INTRO.search(op_text):
        return ('contradictory_topic',None,who)
    if not linked and (not has_intro or not any(outbound_intro(line.get('text')) for line in operator_lines)):
        return ('unconfirmed_project',None,who)
    status, reason, evidence, method = materialize(raw)
    if status != 'extracted' or not evidence:
        return ('no_reusable_turns',None,who)
    return ('eligible','linked_sheet' if linked else 'verified_operator_intro',who)


async def inspect(pool, owner, project_id, client_id, olga_override=None):
    binding = await pool.fetchrow('''SELECT p.id FROM robot_reply_projects p
       JOIN robot_reply_learning_scripts b ON b.project_id=p.id AND b.owner=p.owner
       WHERE p.id=$1 AND p.owner=$2 AND p.profile='packaging' AND b.client_id=$3 AND b.script='packaging' ''',
       project_id,owner,client_id)
    if not binding:
        raise ValueError('Нет подтверждённой привязки проекта упаковки к компании и сценарию')
    operators = resolve_operators(await call_analysis.operators(pool,client_id), olga_override)
    human_exts = [operators[x] for x in ('olga','oksana') if x in operators]
    if not human_exts:
        raise ValueError('Нет подтверждённых добавочных Ольги или Оксаны')
    rows = await pool.fetch('''SELECT c.id,c.client_id,c.extension,c.direction,c.duration_seconds,c.call_started_at,
          coalesce(nullif(btrim(c.transcript),''),nullif(btrim(a.transcript),'')) AS transcript,
          cs.tab, (s.source_id IS NOT NULL) AS already_imported
        FROM calls c LEFT JOIN astra_analysis a ON a.call_id=c.id
        LEFT JOIN callcenter_call_sources cs ON cs.call_id=c.id AND cs.client_id=c.client_id
        LEFT JOIN robot_reply_learning_sources s ON s.owner=$2 AND s.project_id=$3 AND s.source_type='human' AND s.source_id=c.id::text
        WHERE c.client_id=$1 AND c.direction=2 AND c.extension=ANY($4::text[])
          AND (cs.tab=$5 OR (coalesce(nullif(btrim(c.transcript),''),nullif(btrim(a.transcript),''),'') ~* '(картон|гофр|короб|упаковк)'))
        ORDER BY c.id''',client_id,owner,project_id,human_exts,SHEET)
    counts = Counter(); selected = []; by_operator = {}
    seen = set()
    for raw in rows:
        row=dict(raw)
        if row['id'] in seen:
            counts['duplicate_rows'] += 1; continue
        seen.add(row['id'])
        if row['already_imported']:
            counts['already_imported'] += 1; continue
        status, kind, who = classify(row,operators)
        counts[status] += 1
        if status == 'eligible':
            counts[kind] += 1
            status, reason, evidence, method = materialize(row['transcript'])
            item = by_operator.setdefault(who, {'new_calls':0,'new_pairs':0,'first_at':None,'last_at':None})
            item['new_calls'] += 1; item['new_pairs'] += len(evidence)
            at = row.get('call_started_at')
            if at:
                stamp = at.isoformat()
                item['first_at'] = min(item['first_at'] or stamp, stamp)
                item['last_at'] = max(item['last_at'] or stamp, stamp)
            selected.append((row,kind,who))
    existing = await pool.fetch("""SELECT c.extension,c.call_started_at,s.evidence
        FROM robot_reply_learning_sources s JOIN calls c ON c.id::text=s.source_id AND c.client_id=$3
        WHERE s.owner=$1 AND s.project_id=$2 AND s.source_type='human' AND c.extension=ANY($4::text[])""",owner,project_id,client_id,human_exts)
    baseline={}
    for item in existing:
        who=next((name for name in ('olga','oksana') if operators.get(name)==str(item['extension'])),None)
        if not who: continue
        summary=baseline.setdefault(who,{'calls':0,'pairs':0,'first_at':None,'last_at':None})
        evidence=item['evidence']
        if isinstance(evidence,str): evidence=json.loads(evidence)
        summary['calls'] += 1
        summary['pairs'] += sum(1 for entry in (evidence or []) if isinstance(entry,dict) and substantive(entry))
        if item['call_started_at']:
            stamp=item['call_started_at'].isoformat()
            summary['first_at']=min(summary['first_at'] or stamp,stamp)
            summary['last_at']=max(summary['last_at'] or stamp,stamp)
    return {'counts':dict(counts), 'operators':operators, 'selected':selected, 'new_by_operator':by_operator, 'before_by_operator':baseline}


async def run(pool, owner, project_id, client_id, olga_override=None, apply=False):
    data = await inspect(pool,owner,project_id,client_id,olga_override)
    written = 0
    if apply:
        for row, kind, who in data['selected']:
            # Atomic insert protects an existing source even if the regular worker
            # imports this call between inspection and application.
            canonical=json.dumps(row['transcript'],ensure_ascii=False,sort_keys=True,default=str)
            digest=hashlib.sha256(('extract-v2:'+canonical).encode('utf-8')).hexdigest()
            status,reason,evidence,role_method=materialize(row['transcript'])
            inserted=await pool.fetchval("""INSERT INTO robot_reply_learning_sources
                (owner,project_id,source_type,source_id,transcript_hash,status,reason,outcome,metadata,evidence,checked_at)
                SELECT $1,$2,'human',$3,$4,$5,$6,'',$7::jsonb,$8::jsonb,now()
                WHERE EXISTS (SELECT 1 FROM robot_reply_projects p JOIN robot_reply_learning_scripts b
                     ON b.project_id=p.id AND b.owner=p.owner WHERE p.id=$2 AND p.owner=$1
                     AND p.profile='packaging' AND b.client_id=$9 AND b.script='packaging')
                ON CONFLICT(owner,project_id,source_type,source_id) DO NOTHING RETURNING 1""",
                owner,project_id,str(row['id']),digest,status,reason,
                json.dumps({'operator':who,'origin':'archive','attribution':kind,'speaker_attribution':role_method},ensure_ascii=False),
                json.dumps(evidence,ensure_ascii=False),client_id)
            written += bool(inserted)
    if apply:
        after = (await inspect(pool,owner,project_id,client_id,olga_override))['before_by_operator']
    else:
        after = {}
        for who in ('olga','oksana'):
            old = data['before_by_operator'].get(who, {})
            new = data['new_by_operator'].get(who, {})
            if not old and not new: continue
            starts = [x for x in (old.get('first_at'),new.get('first_at')) if x]
            ends = [x for x in (old.get('last_at'),new.get('last_at')) if x]
            after[who] = {'calls':old.get('calls',0)+new.get('new_calls',0),
                          'pairs':old.get('pairs',0)+new.get('new_pairs',0),
                          'first_at':min(starts) if starts else None,'last_at':max(ends) if ends else None}
    return {'mode':'apply' if apply else 'dry-run','project_id':project_id,'client_id':client_id,
            'operators':data['operators'],'counts':data['counts'],'before_by_operator':data['before_by_operator'],
            'new_by_operator':data['new_by_operator'],'after_by_operator':after,'written':written,
            'after_is_projection':not apply}
