"""Discovery of repeated observed exchanges, scoped by the caller to one project.

Observed speech is evidence, not verified project facts or proof of effectiveness.
"""
from __future__ import annotations
import hashlib
import json
import re
from difflib import SequenceMatcher

STOP = frozenset('а и но или ли вы вам вас ваш ваша ваше ваши у на в с со по для из это такой такая так вот можно пожалуйста подскажите скажите вообще'.split())
FILLER = re.compile(r'^(?:да|нет|алло|понятно|хорошо|спасибо|угу|ага|ясно|не знаю|не понял[аи]?)[.!? ]*$', re.I)
DECLINE = re.compile(r'не звоните|не беспокойте|не надо|не интерес|не актуальн|отказ|удалите номер|автоответчик', re.I)
PERSONAL = re.compile(r'\d|@|\[|https?://|[A-Za-z]|\b(?:инн|телефон|адрес|имя|зовут|фамили[яию])\b', re.I)
NAME = re.compile(r'(?:^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\b|(?<![.!?] )(?<!^)\b[А-ЯЁ][а-яё]{2,}\b)')
QUESTION = re.compile(r'^(?:как|какой|какая|какие|какую|когда|где|куда|откуда|зачем|почему|можно ли|возможно ли|что|сколько|подскажите)', re.I)
SAFE_QUESTION = re.compile(r'^(?:как|какой|какая|какие|какую|когда|где|куда|чем|что|сколько|подскажите|скажите|уточните)\b', re.I)
QUESTION_CLAIM = re.compile(r'\b(?:мы|наш\w*|у нас|могу|можем|сделаем|отправим|доставим|гарантиру\w*|бесплатн\w*)\b',re.I)


def norm(text):
    return ' '.join(re.findall(r'[а-я]+', str(text or '').lower().replace('ё','е')))


def tokens(text):
    # Light inflection normalization only; no vector model or cross-project dictionary.
    result=[]
    for word in norm(text).split():
        if word in STOP or len(word)<3: continue
        if len(word)>6:
            word=re.sub(r'(?:иями|ение|ения|ного|ному|ыми|ими|ами|ого|ему|ую|ая|ое|ие|ый|ий|ой|ам|ах|ом|ем|ы|а|и|е|у)$','',word)
        if len(word)>=3: result.append(word)
    return frozenset(result)


def _interrogative(text):
    words=norm(text).split()
    return next((w for w in words if w in {'как','когда','где','куда','откуда','почему','зачем','сколько','какой','какая','какие','какую','что','чем'}), '')


def similar(a,b):
    if _interrogative(a) != _interrogative(b): return False
    if ('не' in norm(a).split()) != ('не' in norm(b).split()): return False
    x,y=tokens(a),tokens(b)
    if len(x)<2 or len(y)<2: return False
    common=len(x & y)
    # Require two exact content stems, strong overlap, and similar surface wording.
    return common>=2 and common/len(x|y)>=0.66 and SequenceMatcher(None,norm(a),norm(b)).ratio()>=0.62


def _eligible(text):
    text=' '.join(str(text or '').split())
    return bool(10<=len(text)<=140 and len(norm(text).split())<=16 and not PERSONAL.search(text)
                and not DECLINE.search(text) and not FILLER.fullmatch(text) and not NAME.search(text))


def _question_only(text):
    return bool(text.endswith('?') and SAFE_QUESTION.match(text) and not QUESTION_CLAIM.search(text)
                and len(norm(text).split())<=12 and _eligible(text))


def build_candidates(sources, profile='generic'):
    """Return review-only candidates; caller MUST pass sources of one project only."""
    projects={(s.get('owner'),s.get('project_id')) for s in sources if s.get('project_id') is not None}
    if len(projects)>1: raise ValueError('discovery sources must belong to one project')
    records=[]
    for source in sources:
        items=source.get('evidence') or []
        if isinstance(items,str): items=json.loads(items)
        sid=str(source.get('source_type',''))+':'+str(source.get('source_id',''))
        if not source.get('source_id'): continue
        for item in items:
            intent = item.get('intent') or ''
            if intent and not str(intent).startswith('unknown:') and not (profile == 'generic' and intent == 'supplier'): continue
            q=' '.join(str(item.get('question') or '').split())
            answer=' '.join(str(item.get('answer') or '').split())
            reaction=' '.join(str(item.get('reaction') or '').split())
            if not (_eligible(q) and (q.endswith('?') or QUESTION.match(q) or (profile == 'generic' and intent == 'supplier')) and _eligible(answer)): continue
            # A meaningful continuation is not proof of success, but excludes dead ends.
            if not reaction or FILLER.fullmatch(reaction) or DECLINE.search(reaction) or PERSONAL.search(reaction) or NAME.search(reaction) or re.search(r'\b(?:не получится|не понимаю|не понял|не поняла|не могу|не смогу|не подходит|не нужно)\b', reaction, re.I): continue
            if item.get('repeat_question') or similar(q,reaction): continue
            records.append({'source_id':sid,'question':q,'answer':answer,'reaction':reaction,
                            'at':item.get('at'),'answer_at':item.get('answer_at'),'reaction_at':item.get('reaction_at')})
    records.sort(key=lambda r:(norm(r['question']),r['source_id'],norm(r['answer'])))
    clusters=[]
    for item in records:
        # Complete-link clustering prevents chained semantic drift.
        group=next((g for g in clusters if all(similar(item['question'],x['question']) for x in g)),None)
        (group if group is not None else clusters.append([]) or clusters[-1]).append(item)
    result=[]
    for items in clusters:
        if len({i['source_id'] for i in items})<2: continue
        replies={}
        for item in items:
            key=norm(item['answer'])
            entry=replies.setdefault(key,{'answer':item['answer'],'sources':set()})
            entry['sources'].add(item['source_id'])
        eligible=[v for _,v in sorted(replies.items()) if len(v['sources'])>=2]
        if not eligible: continue
        chosen=sorted(eligible,key=lambda x:(-len(x['sources']),norm(x['answer'])))[0]
        answer=chosen['answer']; variants=sorted({i['question'] for i in items},key=norm)[:20]
        sources_used=sorted({i['source_id'] for i in items})
        key='discovery:'+hashlib.sha256((_interrogative(variants[0])+'|'+('|'.join(sorted(tokens(variants[0]))))).encode()).hexdigest()[:20]
        fact_needed=not _question_only(answer)
        group={'intent':key,'variants':variants,'answers':[answer],'source_ids':sources_used,
               'repeat_count':0,'outcomes':{},'observations':items[:20]}
        fingerprint=hashlib.sha256(json.dumps({'intent':key,'answer':answer,'fact_needed':fact_needed},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        result.append({'intent':key,'group':group,'question':variants[0],'answer':answer,'triggers':variants,
                       'fingerprint':fingerprint,'fact_needed':fact_needed,
                       'fact_note':'Фактическое утверждение из разговора; необходима проверка директором.' if fact_needed else ''})
    return result
