"""Evidence-led, local synthesis of an immutable call report run.

This is a display version, not a new scoring methodology. Saved scores and
model responses are never edited. An observation is not proved by an arbitrary
matching quote: typed facts and full-dialogue checks provide its support.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata

VERSION = 'extended_review_20261006_v1'
TOKEN = re.compile(r'[-−–—]?\d+(?:[.,]\d+)?(?:\s*%)?|[^\W\d_]+', re.UNICODE)
LABEL = re.compile(r'\[(?:\d+:\d+\s+)?Спикер\s+(\d+)\]|\b(Менеджер|Клиент|Секретарь)\s*:', re.I)


def tokens(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('−', '-').replace('–', '-').replace('—', '-')
    return [re.sub(r'\s+', '', m[0]).replace(',', '.') for m in TOKEN.finditer(value)]


def prose(value):
    """Never emit JSON, incomplete model strings or technical truncation."""
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split()).strip()
    if not value or value.startswith(('{', '[', '```')) or value.endswith(('…', '...')):
        return ''
    return value


@dataclass(frozen=True)
class Turn:
    id: int
    speaker: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Fact:
    kind: str
    value: str
    turn_ids: tuple[int, ...]
    speaker: str
    confirmation: str = 'direct'
    confidence: str = 'supported'


class Evidence:
    def __init__(self, row):
        self.transcript = row.get('transcript') or ''
        analysis = (row.get('primary_analysis') or {}).get('analysis') or {}
        cfg = row.get('configuration') or {}
        self.complete = cfg.get('transcript_complete') is True and not analysis.get('call_cut_short')
        roles = prose(analysis.get('roles'))
        role_map = {}
        for role in ('Менеджер', 'Клиент', 'Секретарь'):
            found = re.search(r'(?:спикер\s*(\d+)\s*(?:—|–|-|:|=|это)?\s*' + role + r'|' + role + r'\s*(?:—|–|-|:|=|это)?\s*спикер\s*(\d+))', roles, re.I)
            if found:
                role_map[next(s for s in found.groups() if s is not None)] = role
        self.turns = []
        labels = list(LABEL.finditer(self.transcript))
        speaker_ids = {label[1] for label in labels if label[1] is not None}
        for i, match in enumerate(labels):
            end = labels[i + 1].start() if i + 1 < len(labels) else len(self.transcript)
            if match[2]:
                speaker = match[2].capitalize()
            elif match[1] in role_map:
                speaker = role_map[match[1]]
            elif len(speaker_ids) == 2 and 'Менеджер' in role_map.values():
                speaker = 'Клиент'
            else:
                speaker = 'Собеседник ' + match[1]
            self.turns.append(Turn(i + 1, speaker, self.transcript[match.end():end].strip(), match.end(), end))
        if not labels and self.transcript.strip():
            self.turns = [Turn(1, 'Собеседник', self.transcript.strip(), 0, len(self.transcript))]

    def locate(self, value):
        if not isinstance(value, str):
            return []
        candidates = [a or b for a, b in re.findall(r'«([^»]+)»|"([^"\n]+)"', value)] or [value]
        result = []
        for candidate in candidates:
            wanted = tokens(candidate)
            if not wanted:
                continue
            for turn in self.turns:
                matches = list(TOKEN.finditer(turn.text))
                got = [tokens(m[0])[0] for m in matches]
                for start in range(len(got) - len(wanted) + 1):
                    if got[start:start + len(wanted)] == wanted:
                        # Print the actual source typography, never a reconstruction.
                        source = turn.text[matches[start].start():matches[start + len(wanted) - 1].end()]
                        result.append((turn, source))
                        break
                if result and result[-1][0] == turn:
                    break
        return result

    def citation(self, turn):
        if len(turn.text) > 600:
            return ''  # Omit an overlong quote; never cut it in the middle.
        return f'{turn.speaker}: «{turn.text}»'


def has(pattern, text):
    return bool(re.search(pattern, text, re.I))


MATERIAL = r'материал|предложени|\bКП\b|прайс|тариф|расцен|файлом|файл'
SEND = r'отправлю|пришлю|вышлю|скину|направлю|напишу|отправим'
REQUEST = r'отправ|пришл|скин|вышл|направ|получ|файлом'
CALL = r'перезвон|созвон|свяж|позвон|обсудим|встре[тч]|увидимся'
TIME = r'послезавтра|сейчас|сегодня|завтра|(?:в|во|к|до)\s+(?:понедельник\w*|вторник\w*|сред[ауеы]|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)|\b\d{1,2}:\d{2}\b|\b\d{1,2}[./]\d{1,2}\b|через\s+\w+\s+(?:дн\w*|недел\w*|месяц\w*)|на\s+следующей\s+неделе'
CONSENT = r'\bда\b|давайте|соглас|хорошо|удобно|удобнее|подходит|договорились'
RETURN_QUESTION = r'когда|какой\s+срок|какое\s+время|ориентир'
NEGATIVE = r'\bне\s+(?:надо|нужно|отправ|присыл|удобн|хочу|перезвон|позвон|созвон|свяж|обсуд|встрет|увид)|\bнет\b|не\s+давайте'


def time_value(text):
    return next((m[0] for m in re.finditer(TIME, text, re.I)
                 if not re.search(r'\bне\s*$', text[:m.start()], re.I)), None)


def clauses(text):
    return re.split(r'(?<=[.!?;])\s+|,\s*(?=(?:а\s+)?(?:отправ|пришл|скин|перезвон|позвон|созвон|свяж))', text, flags=re.I)


def extract_facts(evidence):
    facts = []
    def add(kind, value, turns, confirmation='direct'):
        facts.append(Fact(kind, value, tuple(t.id for t in turns), turns[-1].speaker, confirmation))
    for i, turn in enumerate(evidence.turns):
        text = turn.text
        prior = evidence.turns[max(0, i - 2):i]
        following = evidence.turns[i + 1:i + 3]
        if turn.speaker == 'Клиент':
            if has(REQUEST, text) and has(MATERIAL, text) and not has(NEGATIVE, text):
                add('materials_request', 'Клиент попросил прислать материалы.', [turn])
            if (has(r'производств|изготовлен', text) and has(r'трудност|сложност|проблем|сбой|задерж', text)
                    and not has(r'нет\s+(?:проблем|трудност)|без\s+проблем|не\s+в\s+производств', text)):
                add('production_context', 'Клиент упомянул трудности с производством.', [turn])
            if has(r'отлож|перенес|пока.*не.*(?:реализ|запус|готов)|не.*(?:выш|дош).*реализ|не\s+начал|не\s+запущ', text):
                add('postponement', 'Клиент сообщил, что запуск пока отложен.', [turn])
            if has(r'бюджет|не\s+хватает\s+денег|нет\s+денег', text):
                add('budget_mention', 'Клиент упомянул бюджет.', [turn])
        if turn.speaker == 'Менеджер' and has(SEND, text) and not has(NEGATIVE, text):
            context = [t for t in prior if t.speaker == 'Клиент' and has(MATERIAL, t.text) and has(REQUEST, t.text)]
            if has(MATERIAL, text) or context:
                add('materials_promise', 'Менеджер предложил отправить материалы.', context[-1:] + [turn], 'linked_turns')
                accepted = next((t for t in following[:1] if t.speaker == 'Клиент' and has(CONSENT, t.text) and not has(NEGATIVE, t.text)), None)
                if context or accepted:
                    add('materials_agreed', 'Согласована отправка материалов.', context[-1:] + [turn] + ([accepted] if accepted else []), 'linked_turns')
                send_clause = next((part for part in clauses(text) if has(SEND, part)), '')
                deadline = time_value(send_clause)
                if deadline:
                    add('materials_deadline', deadline, [turn])
        for name, pattern in [('Telegram', r'телеграм|telegram'), ('электронная почта', r'почт|e-?mail')]:
            if has(pattern, text) and not has(NEGATIVE, text):
                if turn.speaker == 'Клиент' and has(CONSENT + '|' + REQUEST, text):
                    add('channel', name, [turn])
                elif turn.speaker == 'Менеджер' and has(SEND + '|' + REQUEST, text):
                    accepted = next((t for t in following[:1] if t.speaker == 'Клиент' and has(CONSENT, t.text) and not has(NEGATIVE, t.text)), None)
                    if accepted:
                        add('channel', name, [turn, accepted], 'linked_turns')
        if turn.speaker == 'Менеджер' and has(RETURN_QUESTION, text) and has(r'верн|проект|запуск|реализ|перезвон|созвон|обсуд|связ|встрет', text):
            add('return_question', 'Менеджер спросил об ориентире продолжения.', [turn])
        call_clause = next((part for part in clauses(text) if has(CALL, part)), '')
        if call_clause:
            add('callback_mention', call_clause, [turn])
        if call_clause and time_value(call_clause) and not has(NEGATIVE, call_clause):
            if turn.speaker == 'Менеджер':
                add('callback_proposal', text, [turn])
                accepted = next((t for t in following[:1] if t.speaker == 'Клиент' and has(CONSENT, t.text) and not has(NEGATIVE, t.text)), None)
                if accepted and (not time_value(accepted.text) or tokens(time_value(accepted.text)) == tokens(time_value(call_clause))):
                    add('agreed_callback', text, [turn, accepted], 'linked_turns')
            elif turn.speaker == 'Клиент' and has(r'давайте|перезвоните|позвоните|свяжитесь|созвонимся|\bда\b', call_clause) and not has(r'может|наверное', call_clause):
                add('agreed_callback', text, [turn])
    # Duplicate facts share a type and value; retain all provenance.
    merged = {}
    for fact in facts:
        key = (fact.kind, fact.value)
        if key in merged:
            old = merged[key]
            merged[key] = Fact(fact.kind, fact.value, tuple(dict.fromkeys(old.turn_ids + fact.turn_ids)), fact.speaker, 'linked_turns')
        else:
            merged[key] = fact
    return list(merged.values())


def classification(row, evidence):
    candidates = []
    for name in ('combined', 'course'):
        data = (row.get('auxiliary') or {}).get(name) or {}
        c = data.get('classification') or {}
        if (isinstance(c.get('confidence'), (int, float)) and c['confidence'] >= .75
                and c.get('stage') not in {None, 'unknown'} and c.get('sales_role') not in {None, 'unknown'}
                and c.get('product') not in {None, 'unknown'}
                and any(evidence.locate(q) for q in c.get('evidence') or [])):
            candidates.append(c)
    if not candidates or len({(c['stage'], c['sales_role'], c['product']) for c in candidates}) > 1:
        return {}, bool(candidates)
    return candidates[0], False


ALIASES = {'concrete_commitment': 'next_step', 'call_reason': 'clarity', 'gatekeeper_passed': 'honest_transfer',
           'decision_influence': 'checks_role', 'no_early_pitch': 'relevant_presentation'}
ABSENCE = {
    'checks_role': ('Не выяснены участники решения', r'кто.*(?:реш|участв|соглас)|как.*решен|вы.*решен',
                    'Неясно, с кем согласовывать дальнейшие условия.', 'Кто, кроме вас, участвует в согласовании условий?'),
    'checks_understanding': ('Не получена обратная связь по предложению', r'что.*(?:дума|понят)|как.*предлож|подходит|насколько.*понят',
                             'Предложение может остаться непонятным клиенту.', 'Как вам такой вариант и что нужно уточнить?'),
    'implication_questions': ('Не уточнены последствия названной проблемы', r'как.*сказыв|во\s+что.*обход|сколько.*теря|чем.*гроз',
                             'Труднее понять, насколько срочно клиенту нужно решение.', 'Как это затруднение влияет на вашу работу?'),
}
DIRECT = {
    'clarity': ('Объяснили предмет разговора', r'обсуд|предлаг|звоню.*(?:повод|компани)|помог|решен'),
    'relevant_presentation': ('Связали предложение с задачей клиента', r'помог|реш|для\s+ва|чтобы'),
    'business_diagnosis': ('Уточнили показатели работы клиента', r'бюджет|заяв|конверс|трафик|средний\s+чек'),
    'checks_role': ('Уточнили участников решения', r'кто|решени|согласован|руководител'),
    'implication_questions': ('Уточнили последствия проблемы', r'сказыва|обходит|теря|гроз'),
    'checks_understanding': ('Проверили понимание предложения', r'понят|подходит|дума|предлож'),
}


def build(row):
    """A structured, source-attributed view; raw criteria stay in diagnostics."""
    evidence = Evidence(row)
    facts = extract_facts(evidence)
    context = (row.get('metadata') or {}).get('fragment_context') or {}
    # Optional, explicitly labelled input for a supplied example, not a claim
    # about the recording. Production capture never manufactures this context.
    if context.get('source') == 'user_prompt' and not evidence.complete:
        if context.get('postponement') is True:
            facts.append(Fact('reported_postponement', 'В описании фрагмента указано, что клиент отложил запуск.', (),
                              'Описание фрагмента', 'provided_context', 'context_only'))
        if context.get('production_difficulties') is True:
            facts.append(Fact('reported_production', 'В описании фрагмента упомянуты производственные трудности.', (),
                              'Описание фрагмента', 'provided_context', 'context_only'))
    by_kind = {f.kind: f for f in facts}
    c, ambiguous_profile = classification(row, evidence)
    primary = row.get('primary_analysis') or {}
    rows = (primary.get('analysis') or {}).get('rows') or []
    stage = c.get('stage')
    strengths, improvements, uncertainty = [], [], []
    def finding(key, title, effect, suggestion='', refs=(), status='supported', sources=()):
        return dict(key=key, title=title, effect=effect, suggestion=suggestion, turn_ids=list(refs),
                    confirmation=status, confidence='supported' if status != 'uncertain' else 'unknown', sources=list(sources))
    if 'materials_request' in by_kind and 'materials_promise' in by_kind:
        f = by_kind['materials_promise']
        strengths.append(finding('materials', 'Предложили материалы в ответ на просьбу клиента',
                                 'Клиент выбрал удобный формат получения информации.', refs=f.turn_ids))
    if 'channel' in by_kind:
        f = by_kind['channel']
        strengths.append(finding('channel', 'Согласовали канал отправки: ' + f.value,
                                 'Понятно, куда направить материалы.', refs=f.turn_ids))
    callback = by_kind.get('agreed_callback')
    if callback:
        strengths.append(finding('next_step', 'Согласовали следующий разговор',
                                 'Продолжение привязано к названному времени и согласию клиента.', refs=callback.turn_ids))
    elif 'materials_request' in by_kind:
        partial = not evidence.complete
        asked = 'return_question' in by_kind
        if 'callback_proposal' in by_kind:
            title = 'Время следующего контакта предложено, но согласие не установлено'
        elif 'callback_mention' in by_kind:
            title = 'Согласование следующего контакта требует проверки'
        elif asked:
            title = 'Ориентир продолжения не получен'
        else:
            title = 'Не согласован контакт после отправки материалов'
        fact_text = ('В доступном фрагменте следующий контакт не согласован.' if partial else
                     'Проверен весь доступный разговор: согласованного следующего контакта нет.')
        if asked:
            fact_text += ' Вопрос о продолжении звучал; подтверждённого ответа на него не видно.'
        if 'callback_mention' in by_kind and not 'callback_proposal' in by_kind:
            fact_text = 'В записи есть реплики о продолжении, но их недостаточно для уверенного вывода о сроке и согласии клиента.'
            uncertainty.append('Согласование следующего контакта требует проверки по связанным репликам.')
        improvements.append(finding('next_step', title,
                          fact_text + ' Продолжение обсуждения остаётся неопределённым.',
                          'Когда вам удобно посмотреть материалы и снова обсудить условия?',
                          refs=tuple(t.id for t in evidence.turns), status='uncertain' if 'callback_mention' in by_kind and 'callback_proposal' not in by_kind else ('full_dialogue_absence' if evidence.complete else 'fragment_absence')))
    if ('postponement' in by_kind or 'reported_postponement' in by_kind) and not callback and 'return_question' not in by_kind:
        # Same actionable gap as next contact: merge by meaning, not quote count.
        if improvements:
            improvements[0]['suggestion'] = 'Когда ориентировочно планируете вернуться к запуску? Когда будет удобно снова обсудить условия?'
        else:
            improvements.append(finding('next_step', 'Не установлен ориентир возвращения к проекту',
                    ('В разговоре' if evidence.complete else 'В доступном фрагменте') + ' срок возвращения не указан; продолжение остаётся неопределённым.',
                    'Когда ориентировочно планируете вернуться к запуску?', refs=tuple(t.id for t in evidence.turns),
                    status='full_dialogue_absence' if evidence.complete else 'fragment_absence'))

    candidates = {}
    for source, data in [('primary', {'rows': rows})] + [(name, (row.get('auxiliary') or {}).get(name) or {}) for name in ('course', 'combined')]:
        for r in data.get('rows') or []:
            native = r.get('key'); key = ALIASES.get(native, native)
            if not r.get('applicable', True) or r.get('status') in {'not_applicable', 'insufficient_data'}:
                continue
            status = r.get('status') or ('passed' if r.get('passed') else 'failed')
            if native == 'talk_share':
                continue  # Text/counts cannot establish time share.
            if key == 'honest_transfer' and stage != 'gatekeeper':
                continue
            if stage in {'presentation', 'demo_preparation', 'demo_results', 'support'} and native in {'sold_meeting', 'problem_over_situational', 'insight', 'implication_questions', 'no_early_pitch', 'decision_influence'}:
                continue
            candidates.setdefault(key, []).append((status, source, r))
    for key, items in candidates.items():
        if key == 'next_step':
            continue  # Reconstructed materials/contact facts resolve coarse disagreement.
        states = {status for status, _, _ in items}
        if len(states) > 1:
            if key in ABSENCE:
                asked = any(t.speaker == 'Менеджер' and has(ABSENCE[key][1], t.text) for t in evidence.turns)
                if asked:
                    items = [item for item in items if item[0] == 'passed']
                elif evidence.complete:
                    items = [item for item in items if item[0] == 'failed']
                else:
                    uncertainty.append('По доступной части записи нельзя уверенно проверить, уточнялся ли этот вопрос.')
                    continue
            else:
                uncertainty.append('Проверки расходятся по одному из действий; в записи недостаточно оснований для однозначного вывода.')
                continue
        status = items[0][0]
        sources = [source for _, source, _ in items]
        if status == 'failed' and key in ABSENCE and evidence.complete:
            title, pattern, risk, suggestion = ABSENCE[key]
            if not any(t.speaker == 'Менеджер' and has(pattern, t.text) for t in evidence.turns):
                # For consequences, require an actual problem, not an unrelated quote.
                if key == 'implication_questions' and not any(t.speaker == 'Клиент' and has(r'проблем|трудност|сложност|теря|не\s+устраив', t.text) for t in evidence.turns):
                    continue
                improvements.append(finding(key, title, risk, suggestion, refs=tuple(t.id for t in evidence.turns),
                                            status='full_dialogue_absence', sources=sources))
        elif status == 'passed' and key in DIRECT:
            title, pattern = DIRECT[key]
            found = next((pair for _, _, r in items for pair in evidence.locate(r.get('evidence'))
                          if pair[0].speaker == 'Менеджер' and has(pattern, pair[1])), None)
            question_keys = {'business_diagnosis', 'checks_role', 'implication_questions', 'checks_understanding'}
            if found and key in question_keys and '?' not in found[0].text:
                continue
            response = next((t for t in evidence.turns if found and t.id == found[0].id + 1 and t.speaker == 'Клиент' and len(tokens(t.text)) >= 3), None)
            if found and response and key not in {i['key'] for i in strengths}:
                if key == 'clarity' and len(tokens(found[1])) < 7:
                    continue
                strengths.append(finding(key, title, 'Получен ответ клиента, который можно использовать для дальнейшего обсуждения.', refs=(found[0].id, response.id), sources=sources))

    # Commercial truth needs an approved contradiction, not an empty catalog.
    claims = [claim for name in ('course', 'combined') for claim in ((row.get('auxiliary') or {}).get(name) or {}).get('commercial_claims') or []]
    for claim in claims:
        found = evidence.locate(claim.get('evidence'))
        if claim.get('verdict') == 'contradicted' and found and claim.get('record_id'):
            improvements.insert(0, finding('factual_accuracy', 'Условие расходится с утверждённым источником',
                'Клиент может ожидать условия, которые не действуют для этого предложения.',
                'Сверю это условие с действующим документом и пришлю подтверждение.', refs=(found[0][0].id,), sources=('approved_catalog',)))
            break

    cfg = row.get('configuration') or {}
    critical = set((cfg.get('verdict') or {}).get('critical') or [])
    for improvement in improvements:
        native_keys = {key for key in critical if ALIASES.get(key, key) == improvement['key']}
        improvement['critical'] = bool(native_keys)  # No invented new critical errors.
    weights = {}
    for item in rows:
        key = ALIASES.get(item.get('key'), item.get('key'))
        weight = item.get('weight')
        if isinstance(weight, (int, float)):
            weights[key] = max(weights.get(key, 0), weight)
    improvements.sort(key=lambda i: (not i['critical'], -weights.get(i['key'], 0)))
    reasons = []
    if any(r.get('key') == 'talk_share' and r.get('applicable', True) for r in rows):
        reasons.append('доля времени речи не установлена: число реплик не измеряет длительность')
    if primary.get('score') is not None and not rows:
        reasons.append('основания критериев для этого балла в истории не сохранены')
    if (primary.get('analysis') or {}).get('call_cut_short'):
        reasons.append('разговор обрывается и материала недостаточно')
    if stage in {'presentation', 'demo_preparation', 'demo_results', 'support'}:
        reasons.append('шкала назначения встречи ограниченно подходит текущему этапу')
    if ambiguous_profile:
        reasons.append('этап разговора определяется неоднозначно')
    if uncertainty:
        reasons.append(uncertainty[0].rstrip('.').lower())
    return dict(version=VERSION, facts=[asdict(f) for f in facts], turns=[asdict(t) for t in evidence.turns],
                transcript_complete=evidence.complete, classification=c, strengths=strengths[:3],
                improvements=improvements[:3], score_reasons=list(dict.fromkeys(reasons)),
                score=primary.get('score'), score_version=cfg.get('score_prompt_version'),
                has_score=primary.get('score') is not None, critical_check_complete=evidence.complete and bool(critical),
                uncertain=bool(uncertainty), facts_by_kind=by_kind, evidence=evidence)


def render(row, model=None):
    from .render import header, redact_phone
    from methodology.profiles import ROLE_LABELS, PRODUCT_LABELS, STAGE_LABELS
    model = model or build(row)
    evidence, facts = model['evidence'], model['facts_by_kind']
    blocks = ['📞 Расширенный разбор\n' + header(row).removeprefix('📞 ')]
    c = model['classification']
    if c:
        blocks[0] += '\n' + ' · '.join(labels[c[key]] for labels, key in
            [(ROLE_LABELS, 'sales_role'), (PRODUCT_LABELS, 'product'), (STAGE_LABELS, 'stage')])
    result = []
    context = []
    for kind in ('postponement', 'materials_request'):
        if kind in facts:
            context.append(facts[kind].value)
    if 'production_context' in facts:
        context.append('Клиент упомянул производственные трудности.')
    if 'reported_postponement' in facts:
        context.append(facts['reported_postponement'].value)
    if context:
        result.append(' '.join(context))
    if 'materials_agreed' in facts:
        agreed_line = 'Согласована отправка материалов'
        if 'channel' in facts:
            agreed_line += ' по электронной почте' if facts['channel'].value == 'электронная почта' else ' в ' + facts['channel'].value
        if 'materials_deadline' in facts:
            agreed_line += '; срок отправки — ' + facts['materials_deadline'].value
        result.append(agreed_line + '.')
    if 'materials_request' in facts:
        result.append('Просьба о материалах сама по себе не подтверждает намерение купить.')
    if 'agreed_callback' in facts:
        result.append('Следующий разговор согласован; подробности — в действиях ниже.')
    elif 'materials_request' in facts or 'postponement' in facts:
        result.append(('В разговоре' if evidence.complete else 'В доступном фрагменте') +
                      (' время следующего контакта предложено, но согласие не установлено.' if 'callback_proposal' in facts else
                       ' согласование следующего контакта требует проверки.' if 'callback_mention' in facts else ' срок следующего контакта не согласован.'))
    if not result:
        client = next((t for t in reversed(evidence.turns) if t.speaker == 'Клиент' and len(tokens(t.text)) >= 4), None)
        result = ['Позиция клиента: ' + evidence.citation(client)] if client and evidence.citation(client) else ['Доступных данных недостаточно, чтобы установить результат разговора.']
    blocks.append('1. Итог звонка\n' + '\n'.join(result))

    used_quotes = set()
    def cite_refs(refs):
        for identifier in refs:
            if identifier in used_quotes:
                continue
            turn = next((t for t in evidence.turns if t.id == identifier), None)
            if turn and evidence.citation(turn):
                used_quotes.add(identifier)
                return '\n' + evidence.citation(turn)
        return ''
    if model['strengths']:
        lines = ['• ' + i['title'] + '. ' + i['effect'] + cite_refs(i['turn_ids']) for i in model['strengths']]
        blocks.append('2. Что получилось хорошо\n' + '\n'.join(lines))
    if model['improvements']:
        lines = []
        for item in model['improvements']:
            title = ('Критичный недостаток: ' if item['critical'] else 'Точка улучшения: ') + item['title']
            lines.append('• ' + title + '.\n' + item['effect'] + '\nКак лучше: «' + item['suggestion'] + '»')
        blocks.append('3. Что нужно улучшить\n' + '\n\n'.join(lines))
    elif model['critical_check_complete'] and not model['uncertain']:
        # Do not claim all errors are absent just because synthesis found none.
        critical = set((row['configuration'].get('verdict') or {}).get('critical') or [])
        checked = [r for r in row['primary_analysis']['analysis'].get('rows') or [] if r.get('key') in critical]
        if len(checked) == len(critical) and all(r.get('passed') or not r.get('applicable', True) for r in checked):
            blocks.append('3. Что нужно улучшить\nПодтверждённых критичных ошибок в проверенных критериях нет.')
    if 'postponement' in facts and 'production_context' in facts:
        f = facts['production_context']
        manager = next((t for t in evidence.turns if t.id > max(f.turn_ids) and t.speaker == 'Менеджер'), None)
        lines = ['Клиент объясняет отсрочку производственными трудностями.' + cite_refs(f.turn_ids)]
        if manager:
            lines.append('Ответ менеджера:' + cite_refs((manager.id,)))
            if 'materials_promise' in facts or has(r'понял|понима', manager.text):
                lines.append('Переход к материалам уместен: он позволяет продолжить общение без давления на запуск.')
        blocks.append('4. Причина отсрочки\n' + '\n'.join(lines))
    elif 'reported_production' in facts:
        blocks.append('4. Причина отсрочки\nВ описании примера названы производственные трудности. Точная реплика клиента не предоставлена; причины и их детали нужно сверить с записью.\nВ доступных репликах менеджер предлагает материалы, а клиент выбирает удобный формат и канал.')
    else:
        for name in ('combined', 'course'):
            items = ((row.get('auxiliary') or {}).get(name) or {}).get('objections') or []
            found = next((pair for item in items for pair in evidence.locate(item.get('evidence')) if pair[0].speaker == 'Клиент'), None)
            if found:
                if found[0].id not in used_quotes:
                    lines = ['Позиция клиента:' + cite_refs((found[0].id,))]
                    response = next((t for t in evidence.turns if t.id == found[0].id + 1 and t.speaker == 'Менеджер'), None)
                    if response:
                        lines.append('Ответ менеджера:' + cite_refs((response.id,)))
                    lines.append('Уточните, что именно мешает продолжению, и ответьте на названное затруднение.')
                    blocks.append('4. Возражение или причина отсрочки\n' + '\n'.join(lines))
                break

    agreed, open_questions, actions = [], [], []
    if 'materials_agreed' in facts:
        deadline = facts.get('materials_deadline'); channel = facts.get('channel')
        line = 'Отправить материалы. Ответственный — менеджер'
        line += '; канал — ' + channel.value if channel else '; канал не установлен'
        line += '; срок отправки — ' + deadline.value if deadline else '; срок отправки не установлен'
        # Print the send promise itself, not channel consent as proof of deadline.
        agreed.append(line + '.' + cite_refs(facts['materials_promise'].turn_ids[-1:]))
        actions.append('Отправить обещанные материалы; факт отправки по разговору не проверяется.')
        if not channel:
            open_questions.append('Куда отправить материалы.')
        if not deadline:
            open_questions.append('Когда отправить материалы.')
    elif 'materials_promise' in facts:
        open_questions.append('Согласие клиента получить предложенные материалы.')
        actions.append('Уточнить согласие клиента и условия отправки материалов.')
    if 'agreed_callback' in facts:
        callback = facts['agreed_callback']
        agreed.append('Следующий разговор — ' + callback.value.rstrip('.!?') + '.' + cite_refs(callback.turn_ids))
        actions.append('Провести следующий разговор в согласованное время.')
    else:
        open_questions.append('Срок и согласие на следующий контакт.')
        if 'postponement' in facts or 'reported_postponement' in facts:
            open_questions.append('Ориентир возвращения к запуску.')
        actions.append('Уточнить подходящий срок продолжения и получить согласие клиента на следующий контакт.')
    if not actions:
        actions.append('Уточнить цель продолжения, ответственное действие и срок; неизвестные договорённости не считать достигнутыми.')
    blocks.append('5. Договорённости и следующие действия\nСогласовано с клиентом:\n' +
                  ('\n'.join('• ' + s for s in agreed) or 'Подтверждённая договорённость не установлена.') +
                  '\nНе согласовано:\n' + ('\n'.join('• ' + s for s in open_questions) or 'Дополнительных пробелов в подтверждённом шаге не выявлено.') +
                  '\nРекомендуем менеджеру:\n' + '\n'.join('• ' + s for s in actions))
    if model['improvements']:
        focus = model['improvements'][0]
        exercise = ('Отработайте вопрос о возвращении к проекту и дождитесь ответа. Затем предложите подходящее время следующего контакта.'
                    if 'postponement' in facts or 'reported_postponement' in facts else 'Разыграйте этот момент с коллегой: задайте предложенный вопрос, дождитесь ответа и используйте его для продолжения.')
        skill = 'согласование конкретного продолжения' if focus['key'] == 'next_step' else focus['title'].lower()
        blocks.append('6. Что потренировать\nНавык: ' + skill + '.\n' + exercise)
    quality = []
    if model['has_score']:
        version = 'версия ' + str(model['score_version']) if model['score_version'] else 'версия в истории не указана'
        reasons = model['score_reasons']
        quality.append(f"Сохранённая оценка: {model['score']}/100 — исходная шкала холодного звонка ({version}).")
        if reasons:
            quality.append('Требует проверки, потому что ' + '; '.join(reasons[:2]) + '.')
        factors = []
        for item in model['improvements'][:2]:
            factors.append(item['title'].lower())
        if factors:
            quality.append('Подтверждённые точки улучшения: ' + '; '.join(factors) + '. Они не пересчитывают сохранённый балл.')
    else:
        quality.append('Надёжная числовая оценка для этого разбора недоступна.')
    if not evidence.complete:
        quality.append('Полнота расшифровки не подтверждена: выводы об отсутствии действий относятся только к доступному тексту.')
    if not model['classification']:
        quality.append('Тип контакта и этап не установлены достаточно уверенно.')
    blocks.append('7. Оценка и достоверность\n' + '\n'.join(quality))
    return redact_phone('\n\n'.join(blocks), row)


def sections(text, limit=3700):
    """Pack complete sections. Oversized atomic sections require a document."""
    from .render import units
    blocks = re.split(r'\n\n(?=[1-7]\. )', text)
    if any(units(block) > limit for block in blocks):
        return None
    chunks, current = [], ''
    for block in blocks:
        trial = current + ('\n\n' if current else '') + block
        if units(trial) > limit:
            chunks.append(current); current = block
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks
