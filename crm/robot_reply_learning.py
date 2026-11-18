"""Проектные источники обучения. Никакой автоматической публикации ответов."""
from __future__ import annotations
import hashlib
import json
import re


def normal(text):
    return re.sub(r'[^\w]+', ' ', str(text or '').lower().replace('ё', 'е'), flags=re.UNICODE).strip()


def redact(text):
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}", "[почта]", str(text or ''))
    text = re.sub(r"(?<!\w)(?:\+?7|8)[\s(\-]*\d(?:[\s()\-]*\d){9}(?!\d)", "[телефон]", text)
    text = re.sub(r'(?i)(меня зовут|мо[её] имя|меня зовут по имени)\s+[А-ЯЁ][а-яё-]+', r'\1 [имя]', text)
    return ' '.join(text.split())[:700]


INTENTS = (
    ('refuse', r'не звоните|не беспокойте|уберите (?:мой )?номер|удалите (?:мой )?номер'),
    ('location', r'где (?:вы |у вас )?(?:находитесь|производство)'),
    ('color', r'цвета? какие|какие цвета|какого цвета'),
    ('assortment', r'каки[еуюх].{0,20}(?:короб|упаковк)|ассортимент|что (?:вы )?(?:производите|делаете)'),
    ('print', r'печать|печат[ьи]|логотип|флексограф'),
    ('profile', r'профил[ьяюе]|профиль|\b(?:BE|BC|EB|БЕ|БЦ)\b'),
    ('material', r'гофрокартон|материал|тр[её]хслойн|пятислойн|мелованн|(?:какой|каким|какого|какую|каких)\s+картон'),
    ('price', r'цен[аыуе]|стоимост|сколько стоит|поч[её]м|прайс'),
    ('supplier', r'поставщик|уже (?:работаем|закупаем)|есть с кем работаем'),
    ('busy', r'занят|некогда|неудобно|перезвоните|перезвонить'),
    ('no_request', r'не искал|не оставлял.{0,15}заяв|не подавал.{0,15}заяв'),
    ('no_need', r'\bне\s+нужн|\bне\s+актуальн|\bнет\s+потребност|\bничего\s+не\s+надо|\bуже\s+купил[аи]?|\bуже\s+закупил[аи]?'),
    ('no_interest', r'не интерес|не интересно'),
    ('call_reason', r'откуда (?:вы|номер)|кто (?:это|вы)|зачем звон|по какому вопросу|откуда у вас'),
    ('repeat', r'не понял|не поняла|повторите|что вы сказали'),
)
SUGGESTIONS = {
    'refuse': 'Понял вас. Всего доброго.',
    'price': 'Сейчас не могу назвать стоимость. Подскажите, какие параметры для вас важны?',
    'supplier': 'Понял, вы уже работаете с поставщиком. Подскажите, рассматриваете ли другие предложения?',
    'busy': 'Понимаю. Когда вам будет удобно поговорить?',
    'no_request': 'Понял, спасибо за ответ. Всего доброго.',
    'call_reason': 'Звоню, чтобы уточнить, актуально ли для вас наше предложение. Удобно коротко обсудить?',
    'repeat': 'Извините, скажу иначе. Подскажите, удобно сейчас поговорить?',
    'unknown': 'Понял вас. Подскажите, пожалуйста, что для вас сейчас важно?',
}


REPEAT_SUGGESTIONS = {
    'price': 'Понимаю, вам важна цена. Сейчас не могу назвать стоимость. Что именно вам нужно?',
    'supplier': 'Понял, поставщик уже есть. Есть ли смысл обсудить альтернативу?',
    'busy': 'Понимаю, сейчас неудобно. В какое время можно перезвонить?',
    'repeat': 'Извините, объясню короче. Удобно сейчас поговорить?',
}


def intent_of(text):
    for label, pattern in INTENTS:
        if re.search(pattern, text, re.I):
            return label
    # Unknown clusters are exact normalized phrases; no speculative semantic matching.
    return 'unknown:' + normal(text)[:160]


def extract(raw, outcome=''):
    from .call_analysis import split_lines
    from .callcenter_verdict import _roles
    if isinstance(raw, str):
        lines = split_lines(raw)
    else:
        lines = []
        for n, item in enumerate(raw or []):
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                role, text = item[:2]; at = None
            elif isinstance(item, dict):
                role, text = item.get('who') or item.get('role'), item.get('text'); at = item.get('at')
            else:
                continue
            role = {'client':'Клиент','robot':'Робот','operator':'Оператор'}.get(str(role).lower(), role)
            lines.append({'speaker': role, 'text': str(text or ''), 'at': at})
    labels = {str(x.get('speaker') or '').lower() for x in lines}
    if labels and labels <= {'клиент','оператор','робот'} and 'клиент' in labels and labels & {'оператор','робот'}:
        operator = next(x.get('speaker') for x in lines if str(x.get('speaker')).lower() in ('оператор','робот'))
        clients = {x.get('speaker') for x in lines if str(x.get('speaker')).lower() == 'клиент'}
        role_evidence = 'explicit'
    else:
        operator, clients = _roles(lines)
        role_evidence = 'inferred_intro'
    if not operator or not clients:
        return 'unattributed', 'speaker_roles_unavailable', []
    merged = []
    for line in lines:
        role = 'client' if line.get('speaker') in clients else 'operator' if line.get('speaker') == operator else ''
        text = redact(line.get('text'))
        if not role or not text:
            if merged: merged.append({'role':'unknown','text':'', 'at':line.get('at')})
            continue
        if merged and merged[-1]['role'] == role:
            merged[-1]['text'] = (merged[-1]['text'] + ' ' + text)[:700]
        else:
            merged.append({'role':role,'text':text,'at':line.get('at')})
    evidence, seen = [], set()
    for i, line in enumerate(merged):
        text = re.sub(r'^(?:алло|здравствуйте|добрый (?:день|вечер)|да|слушаю)[,.! ]*', '', line['text'], flags=re.I).strip()
        if line['role'] != 'client' or len(normal(text)) < 5 or i + 1 >= len(merged) or merged[i+1]['role'] != 'operator':
            continue
        key = normal(text); intent = intent_of(text)
        repeated = key in seen or intent in seen
        seen.update((key,intent))
        reaction = merged[i+2] if i+2 < len(merged) and merged[i+2]['role'] == 'client' else {}
        evidence.append({'question':text,'answer':merged[i+1]['text'],'question_key':key,'intent':intent,
                         'at':line['at'],'answer_at':merged[i+1]['at'],'reaction':reaction.get('text',''),'reaction_at':reaction.get('at'),
                         'repeat_question':repeated,'context':merged[max(0,i-2):i],
                         'outcome':outcome or 'unknown','labels':['observed','unreviewed',role_evidence]})
    return ('extracted','',evidence) if evidence else ('no_pairs','no_adjacent_client_operator_turns',[])


async def ingest_source(pool, owner, project_id, source_type, source_id, raw, outcome='', metadata=None):
    if source_type not in ('human', 'robot') or project_id is None:
        raise ValueError('project and source are required')
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(('extract-v2:' + canonical).encode('utf-8')).hexdigest()
    previous = await pool.fetchrow('SELECT transcript_hash,outcome,metadata FROM robot_reply_learning_sources WHERE owner=$1 AND project_id=$2 AND source_type=$3 AND source_id=$4', owner, project_id, source_type, str(source_id))
    if previous:
        old_meta = previous['metadata']
        if isinstance(old_meta, str): old_meta = json.loads(old_meta)
        if previous['transcript_hash'] == digest and previous['outcome'] == str(outcome or '') and old_meta == (metadata or {}):
            await pool.execute('UPDATE robot_reply_learning_sources SET checked_at=now() WHERE owner=$1 AND project_id=$2 AND source_type=$3 AND source_id=$4', owner, project_id, source_type, str(source_id))
            return 'unchanged'
    status, reason, evidence = extract(raw, outcome)
    return await pool.execute("""INSERT INTO robot_reply_learning_sources
        (owner,project_id,source_type,source_id,transcript_hash,status,reason,outcome,metadata,evidence,checked_at)
        SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,now()
        WHERE EXISTS (SELECT 1 FROM robot_reply_projects WHERE id=$2 AND owner=$1)
        ON CONFLICT(owner,project_id,source_type,source_id) DO UPDATE SET
          transcript_hash=EXCLUDED.transcript_hash,status=EXCLUDED.status,reason=EXCLUDED.reason,
          outcome=EXCLUDED.outcome,metadata=EXCLUDED.metadata,evidence=EXCLUDED.evidence,updated_at=CASE WHEN robot_reply_learning_sources.transcript_hash IS DISTINCT FROM EXCLUDED.transcript_hash THEN now() ELSE robot_reply_learning_sources.updated_at END, checked_at=now()""",
        owner, project_id, source_type, str(source_id), digest, status, reason, str(outcome or ''),
        json.dumps(metadata or {}, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False))


async def backfill(pool, limit=200, only_new=False, owner=None, project_id=None):
    """Повторяемое заполнение; только явно сопоставленные проекты, без курсора потери данных."""
    robots = await pool.fetch("""SELECT r.call_id,r.owner,p.id AS project_id,r.operator,r.outcome,r.data
       FROM robot_calls r JOIN robot_reply_project_campaigns pc ON pc.campaign_id=r.campaign_id
       JOIN robot_reply_projects p ON p.id=pc.project_id AND p.owner=r.owner
       LEFT JOIN robot_reply_learning_sources s ON s.owner=r.owner AND s.project_id=p.id
         AND s.source_type='robot' AND s.source_id=r.call_id
       WHERE (s.source_id IS NULL OR (NOT $2::boolean AND (s.checked_at IS NULL OR s.checked_at < now() - interval '1 day'))) AND ($3::text IS NULL OR p.owner=$3) AND ($4::bigint IS NULL OR p.id=$4)
       ORDER BY s.checked_at ASC NULLS FIRST, r.at LIMIT $1""", limit, only_new, owner, project_id)
    for r in robots:
        data = json.loads(r['data']) if isinstance(r['data'], str) else (r['data'] or {})
        await ingest_source(pool, r['owner'], r['project_id'], 'robot', r['call_id'], data.get('transcript') or [], r['outcome'], {'operator': r['operator']})
    humans = await pool.fetch("""SELECT c.id,c.transcript,min(r.operator) AS operator,b.owner,b.project_id,min(s.checked_at) AS checked_at
       FROM call_analysis_items i JOIN call_analysis_runs r ON r.id=i.run_id AND r.client_id=i.client_id
       JOIN calls c ON c.id=i.call_id AND c.client_id=i.client_id
       JOIN robot_reply_learning_scripts b ON b.client_id=c.client_id AND b.script=i.stages->'script_check'->>'script'
       JOIN robot_reply_projects p ON p.id=b.project_id AND p.owner=b.owner
       LEFT JOIN robot_reply_learning_sources s ON s.owner=b.owner AND s.project_id=b.project_id
          AND s.source_type='human' AND s.source_id=c.id::text
       WHERE r.operator IN ('olga','oksana') AND c.transcript IS NOT NULL AND (s.source_id IS NULL OR (NOT $2::boolean AND (s.checked_at IS NULL OR s.checked_at < now() - interval '1 day'))) AND ($3::text IS NULL OR b.owner=$3) AND ($4::bigint IS NULL OR b.project_id=$4)
       GROUP BY c.id,c.transcript,b.owner,b.project_id
       ORDER BY min(s.checked_at) ASC NULLS FIRST,c.id LIMIT $1""", limit, only_new, owner, project_id)
    for r in humans:
        await ingest_source(pool, r['owner'], r['project_id'], 'human', r['id'], r['transcript'], metadata={'operator': r['operator']})
    return len(robots), len(humans)


async def evidence_for_candidate(pool, owner, project_id, candidate_id):
    """Return only the candidate's matched observations inside its project."""
    rows = await pool.fetch(
        """SELECT s.source_type,s.source_id,s.status,s.reason,s.outcome,s.evidence,k.intent,l.manual,v.triggers,c.source_kind
           FROM robot_reply_learning_links l
           JOIN robot_reply_candidates c ON c.id=l.candidate_id
           JOIN robot_reply_versions v ON v.candidate_id=c.id AND v.version=c.current_version
           LEFT JOIN robot_reply_learning_clusters k ON k.candidate_id=c.id AND k.owner=c.owner AND k.project_id=c.project_id
           JOIN robot_reply_learning_sources s ON s.owner=l.owner AND s.project_id=l.project_id
             AND s.source_type=l.source_type AND s.source_id=l.source_id
           WHERE c.id=$3 AND c.owner=$1 AND c.project_id=$2 AND l.owner=$1 AND l.project_id=$2""",
        owner, project_id, candidate_id)
    result = []
    for row in rows:
        data = dict(row)
        items = data['evidence']
        if isinstance(items, str): items = json.loads(items)
        triggers = data.pop('triggers') or []
        if isinstance(triggers, str): triggers = json.loads(triggers)
        keys = {normal(t) for t in triggers}
        if str(data['intent'] or '').startswith('discovery:') and not data['manual']:
            data['evidence'] = [item for item in items if normal(item.get('question', '')) in keys]
        else:
            data['evidence'] = [item for item in items if data['manual'] or not data['intent'] or (data['source_kind'] == 'guide' and normal(item.get('question','')) in keys) or ((item.get('intent') or intent_of(item.get('question',''))) == data['intent'] and usable_observation(item))]
        result.append(data)
    return result


def hypotheses(sources):
    """Cluster observed statements; outcomes are context, never proof of success."""
    groups = {}
    for source in sources:
        items = source.get('evidence') or []
        if isinstance(items, str):
            items = json.loads(items)
        for item in items:
            key = item.get('intent') or intent_of(item.get('question', ''))
            if not key:
                continue
            group = groups.setdefault(key, {'intent': key, 'variants': [], 'answers': [], 'source_ids': [], 'repeat_count': 0, 'outcomes': {}})
            for field, value in (('variants', item.get('question')), ('answers', item.get('answer')), ('source_ids', str(source.get('source_type', '')) + ':' + str(source.get('source_id', '')))):
                if value and value not in group[field]:
                    group[field].append(value)
            group['repeat_count'] += bool(item.get('repeat_question'))
            outcome = item.get('outcome') or 'unknown'
            group['outcomes'][outcome] = group['outcomes'].get(outcome, 0) + 1
    return list(groups.values())


async def coverage(pool, owner, project_id):
    rows = await pool.fetch("""SELECT source_type,status,reason,count(*)::int AS count
        FROM robot_reply_learning_sources WHERE owner=$1 AND project_id=$2 GROUP BY source_type,status,reason
        ORDER BY source_type,status,reason""", owner, project_id)
    return [dict(row) for row in rows]


PACKAGING_SUGGESTIONS = {
    'price': 'Стоимость зависит от размера, картона и тиража. Для расчёта нужны эти параметры.',
    'assortment': 'Мы производим простые коробки из белого или бурого гофрокартона, конструкция может быть разной.',
    'color': 'Работаем с белым и бурым гофрокартоном.',
    'location': 'Точный адрес производства нужно уточнить.',
    'material': 'Работаем с белым или бурым гофрокартоном: трёхслойным и пятислойным.',
    'print': 'Мы делаем простую флексопечать в один или два цвета.',
    'profile': 'Не могу подтвердить конкретный профиль картона. Это нужно уточнить.',
    'supplier': 'Понимаю, у вас уже есть поставщик. По цене и срокам сейчас всё устраивает?',
    'no_request': 'Понял вас. Спасибо, что уточнили.',
    'no_need': 'Понял, сейчас коробки вам не нужны. Спасибо за ответ.',
    'no_interest': 'Понял вас, спасибо за ответ.',
    'busy': 'Понял вас, спасибо.',
}


PACKAGING_REASK_SUGGESTIONS = {
    'price': 'Понимаю, вопрос о цене важен. Стоимость зависит от размера, картона и тиража.',
    'supplier': 'Понял, поставщик у вас уже есть. Что для вас важно в работе с ним?',
}
GENERIC_ACKS = {'понял спасибо', 'поняла спасибо', 'спасибо за ответ', 'понял вас спасибо', 'поняла вас спасибо', 'хорошо спасибо'}


def usable_observation(item):
    value = str(item.get('question') or '').strip()
    if not 8 <= len(value) <= 180 or re.search(r'\d|\[|@|https?://', value, re.I):
        return False
    if len(re.findall(r'[?!]', value)) > 1 or re.search(r'\s[-—–]\s|\bсмотрите\b|\bговорит\b', value, re.I):
        return False
    if re.search(r'не звоните|не беспокойте|автоответчик|робот|голосов(?:ая|ую) почт', value, re.I):
        return False
    # Multiple topics should be handled by the full dialogue, never a single recording.
    hits = [label for label, pattern in INTENTS if re.search(pattern, value, re.I)]
    # An explicit profile question can mention material too; it must never imply support.
    if 'profile' in hits and set(hits) <= {'profile','material'}: hits = ['profile']
    if hits == ['price'] and not re.fullmatch(r'(?:а\s+)?(?:сколько\s+(?:это\s+)?сто(?:ит|ят)(?:\s+(?:коробк[иа]|коробок|упаковк[аи]))?|как(?:ая|ова)\s+(?:цена|стоимость)(?:\s+(?:коробк[иа]|коробок|упаковк[аи]))?|(?:цена|стоимость)(?:\s+(?:коробк[иа]|коробок|упаковк[аи]))?)(?:[?!.])?', value.strip(), re.I):
        return False
    if hits == ['material']:
        # A product keyword alone can be a website/search or supplier mention.
        # Keep actual material specifications, material questions and requests.
        specification = re.search(r'тр[её]хслойн|пятислойн|мелованн', value, re.I)
        material_question = re.search(
            r'(?:какой|каким|какая|какую|какие|какого|каких|какое|из чего|из какого).{0,35}(?:картон|материал)'
            r'|(?:гофрокартон|материал).{0,35}(?:какой|какая|какие|используете|работаете|бывает)', value, re.I)
        material_request = re.search(
            r'(?:нужен|нужна|нужно|требуется|ищем|используем|закупаем|покупаем|бер[её]м).{0,35}(?:гофрокартон|материал)', value, re.I)
        if not (specification or material_question or material_request):
            return False
    if hits == ['color'] and not re.search(r'короб|картон|упаков', value, re.I):
        return False
    if hits == ['print'] and not re.search(r'\?|(?:есть|делаете|можете|возможн|какая|какую|бывает)', value, re.I):
        return False
    return len(hits) == 1 and hits[0] == item.get('intent')


def build_candidates(sources, profile='generic', guide_enabled=False):
    """Pure proposal rules; only the explicit project profile supplies product facts."""
    if profile not in ('generic', 'packaging'):
        return []
    filtered = []
    for source in sources:
        items = source.get('evidence') or []
        if isinstance(items, str): items = json.loads(items)
        filtered.append({**dict(source), 'evidence':[i for i in items if usable_observation(i)]})
    result = []
    for g in hypotheses(filtered):
        key = g['intent']
        if len(g['source_ids']) < 2 and not (key == 'print' and g['repeat_count'] >= 1):
            continue
        if profile == 'packaging':
            answer = PACKAGING_SUGGESTIONS.get(key)
            if g['repeat_count'] >= 2:
                answer = PACKAGING_REASK_SUGGESTIONS.get(key, answer)
        else:
            # Only repeated, non-factual acknowledgements from independent sources.
            candidates = {}
            for source in filtered:
                for item in source['evidence']:
                    reply = str(item.get('answer') or '').strip()
                    if item.get('intent') == key and normal(reply) in GENERIC_ACKS:
                        candidates.setdefault(normal(reply), {'text':reply, 'sources':set()})['sources'].add(str(source.get('source_type')) + ':' + str(source.get('source_id')))
            eligible = [v['text'] for _,v in sorted(candidates.items()) if len(v['sources']) >= 2]
            answer = eligible[0] if eligible else None
        if not answer:
            continue
        # Stable canonical order and de-duplication prevent version churn on reordering.
        unique = {normal(v):v.strip() for v in g['variants'] if normal(v)}
        variants = [unique[k] for k in sorted(unique)][:20]
        if not variants: continue
        payload = {'question': variants[0], 'answer': answer, 'triggers': variants}
        result.append({'group':g, 'intent':key, **payload,
            'fingerprint':hashlib.sha256(json.dumps({'intent':key, 'answer':answer}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
    from .robot_reply_discovery import build_candidates as discover
    for discovered in discover(sources, profile):
        existing = {normal(t) for candidate in result for t in candidate['triggers']}
        if not existing.intersection(normal(t) for t in discovered['triggers']):
            result.append(discovered)
    if profile == 'packaging' and guide_enabled:
        # Guide is an explicit prior; never count it as a call or approve it automatically.
        templates = {
          'price': (['Сколько стоит?', 'Какая цена?', 'Сколько стоят коробки?'], 'Стоимость зависит от размера, картона и тиража. Какой тираж примерно?', False, ''),
          'assortment': (['Что вы производите?', 'Вы картон производите или что?'], PACKAGING_SUGGESTIONS['assortment'], False, ''),
          'material': (['С каким картоном работаете?', 'Какой материал используете?'], PACKAGING_SUGGESTIONS['material'], False, ''),
          'print': (['Какая печать возможна?', 'Можно нанести логотип?', 'У вас есть офсетная печать?', 'Офсетная печать есть?', 'Афсетная печать есть?'], PACKAGING_SUGGESTIONS['print'], False, ''),
          'quote_requirements': (['Что нужно для расчёта?', 'Какие данные нужны для расчёта?'], 'Для расчёта нужны размер и тираж.', False, ''),
          'supplier': (['У нас есть поставщик.', 'Мы уже работаем с поставщиком.'], 'Понимаю, у вас уже есть поставщик. По цене и срокам сейчас всё устраивает?', False, ''),
          'busy': (['Сейчас неудобно.', 'Я сейчас занят.'], 'Понимаю, сейчас неудобно. Когда лучше перезвонить?', False, ''),
          'location': (['Где находится производство?', 'Где вы находитесь?'], 'Укажите проверенный адрес производства.', True, 'В шпаргалке нет адреса производства. Проверьте и внесите его.'),
        }
        observations = {g['intent']:g for g in hypotheses(filtered)}
        for key, (variants, answer, needed, note) in templates.items():
            existing_topic = next((c for c in result if c['intent'] == key), None)
            if existing_topic:
                # A newly observed wording must recur in two distinct calls before it
                # modifies an approved snapshot; guide variants are explicitly authored.
                counts = {}
                for source in filtered:
                    sid = str(source.get('source_type')) + ':' + str(source.get('source_id'))
                    for item in source['evidence']:
                        if item.get('intent') == key:
                            counts.setdefault(normal(item.get('question','')), set()).add(sid)
                recurring = [v for v in existing_topic['triggers'] if len(counts.get(normal(v), set())) >= 2]
                existing_topic['triggers'] = list(dict.fromkeys(variants + recurring))[:20]
                existing_topic['source_kind'] = 'guide'
                if key in ('price','busy','supplier','location'):
                    if not (key == 'price' and existing_topic['group']['repeat_count'] >= 2):
                        existing_topic['answer'] = answer
                    existing_topic['fact_needed'], existing_topic['fact_note'] = needed, note
                snapshot = {'intent': key, 'answer': existing_topic['answer'], 'fact_needed': bool(existing_topic.get('fact_needed')), 'triggers': sorted(normal(v) for v in existing_topic['triggers'])}
                existing_topic['fingerprint'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                continue
            normalized = {normal(v) for v in variants}
            overlaps = [c for c in result if c['intent'].startswith('discovery:') and normalized.intersection(normal(v) for v in c['triggers'])]
            for c in overlaps: result.remove(c)
            group = observations.get(key) or {'intent':key,'variants':[], 'answers':[], 'source_ids':[], 'repeat_count':0, 'outcomes':{}}
            if overlaps:
                group = {**group, 'source_ids':sorted(set(group['source_ids']).union(*(set(c['group']['source_ids']) for c in overlaps)))}
            fingerprint = hashlib.sha256(json.dumps({'intent':key,'answer':answer,'fact_needed':needed,'triggers':sorted(normal(v) for v in variants)}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            result.append({'intent':key,'group':group,'question':variants[0],'answer':answer,'triggers':variants,
                           'fingerprint':fingerprint,'fact_needed':needed,'fact_note':note,'source_kind':'guide'})
    return result


async def propose(pool, owner, project_id):
    """Generate review-only drafts from recurring observed clusters, never activate them."""
    rows = await pool.fetch("SELECT source_type,source_id,evidence FROM robot_reply_learning_sources WHERE owner=$1 AND project_id=$2 AND status='extracted' ORDER BY source_type,source_id", owner, project_id)
    profile = await pool.fetchval('SELECT profile FROM robot_reply_projects WHERE id=$1 AND owner=$2', project_id, owner)
    mapped = await pool.fetchval('SELECT EXISTS(SELECT 1 FROM robot_reply_learning_scripts WHERE owner=$1 AND project_id=$2)', owner, project_id)
    candidates = build_candidates([dict(r) for r in rows], profile or 'generic', bool(mapped))
    created = 0
    for candidate in candidates:
        key, question, answer, fingerprint = (candidate[k] for k in ('intent', 'question', 'answer', 'fingerprint'))
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1),$2::int)', owner, project_id)
            project = await conn.fetchrow('SELECT id FROM robot_reply_projects WHERE id=$1 AND owner=$2', project_id, owner)
            if not project:
                continue
            cluster = await conn.fetchrow('SELECT * FROM robot_reply_learning_clusters WHERE owner=$1 AND project_id=$2 AND intent=$3 FOR UPDATE', owner, project_id, key)
            if cluster is None:
                cid = await conn.fetchval('INSERT INTO robot_reply_candidates(owner,project_id,source_kind) VALUES($1,$2,$3) RETURNING id', owner, project_id, candidate.get('source_kind','learned'))
                await conn.execute('INSERT INTO robot_reply_versions(candidate_id,version,question,answer,triggers,fact_needed,fact_note) VALUES($1,1,$2,$3,$4::jsonb,$5,$6)', cid, question, answer, json.dumps(candidate['triggers'],ensure_ascii=False), bool(candidate.get('fact_needed')), candidate.get('fact_note',''))
                await conn.execute('INSERT INTO robot_reply_learning_clusters(owner,project_id,intent,candidate_id,fingerprint) VALUES($1,$2,$3,$4,$5)', owner, project_id, key, cid, fingerprint)
                await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action,detail) VALUES($1,1,'learning','draft','Гипотеза по повторяющимся репликам; требует проверки')", cid)
                created += 1
            else:
                cid = cluster['candidate_id']
                current = await conn.fetchrow('SELECT current_version,status FROM robot_reply_candidates WHERE id=$1 AND owner=$2 AND project_id=$3 FOR UPDATE', cid, owner, project_id)
                if current and current['status'] in ('draft','approved') and current['current_version'] == cluster['generated_version'] and cluster['fingerprint'] != fingerprint:
                    new = current['current_version'] + 1
                    await conn.execute('INSERT INTO robot_reply_versions(candidate_id,version,question,answer,triggers,fact_needed,fact_note) VALUES($1,$2,$3,$4,$5::jsonb,$6,$7)', cid, new, question, answer, json.dumps(candidate['triggers'],ensure_ascii=False), bool(candidate.get('fact_needed')), candidate.get('fact_note',''))
                    await conn.execute("UPDATE robot_reply_candidates SET current_version=$2,status='draft',updated_at=now() WHERE id=$1", cid, new)
                    await conn.execute('UPDATE robot_reply_learning_clusters SET fingerprint=$4,generated_version=$5 WHERE owner=$1 AND project_id=$2 AND intent=$3', owner, project_id, key, fingerprint, new)
                    await conn.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action,detail) VALUES($1,$2,'learning','refresh','Изменились источники гипотезы')", cid, new)
            existing = await conn.fetch('SELECT source_type,source_id,manual FROM robot_reply_learning_links WHERE candidate_id=$1 AND owner=$2 AND project_id=$3', cid, owner, project_id)
            desired = set(candidate['group']['source_ids'])
            known = {str(link['source_type']) + ':' + str(link['source_id']) for link in existing}
            for link in existing:
                ident = str(link['source_type']) + ':' + str(link['source_id'])
                if not link['manual'] and ident not in desired:
                    await conn.execute('DELETE FROM robot_reply_learning_links WHERE candidate_id=$1 AND owner=$2 AND project_id=$3 AND source_type=$4 AND source_id=$5 AND NOT manual', cid, owner, project_id, link['source_type'], link['source_id'])
            for source in rows:
                ident = str(source['source_type']) + ':' + str(source['source_id'])
                if ident in desired and ident not in known:
                    await conn.execute('INSERT INTO robot_reply_learning_links(candidate_id,owner,project_id,source_type,source_id) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING', cid, owner, project_id, source['source_type'], source['source_id'])

    return created


async def refresh_all(pool):
    # The watermark advances only after a successful refresh; new source changes racing
    # with refresh remain visible next cycle.
    rows = await pool.fetch("""SELECT p.id,p.owner,now() AS started_at FROM robot_reply_projects p
        WHERE (EXISTS (SELECT 1 FROM robot_reply_learning_scripts sc WHERE sc.owner=p.owner AND sc.project_id=p.id) AND NOT EXISTS (SELECT 1 FROM robot_reply_learning_refresh r WHERE r.owner=p.owner AND r.project_id=p.id)) OR EXISTS (SELECT 1 FROM robot_reply_learning_sources s WHERE s.owner=p.owner AND s.project_id=p.id
          AND s.updated_at > COALESCE((SELECT r.checked_at FROM robot_reply_learning_refresh r
              WHERE r.owner=p.owner AND r.project_id=p.id), '-infinity'::timestamptz))""")
    for row in rows:
        await propose(pool, row['owner'], row['id'])
        await pool.execute("""INSERT INTO robot_reply_learning_refresh(owner,project_id,checked_at) VALUES($1,$2,$3)
          ON CONFLICT(owner,project_id) DO UPDATE SET checked_at=EXCLUDED.checked_at""", row['owner'], row['id'], row['started_at'])
