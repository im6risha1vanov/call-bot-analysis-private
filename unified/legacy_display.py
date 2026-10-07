"""Emoji presentation of legacy_52e4d575 only, never an alternative score.

Uses the saved primary rows/short/detailed response. Evidence utilities only
validate source quotes and material/callback facts; no course classifications,
new evaluation rows, context-only hypotheses or auxiliary reports are used.
"""
from __future__ import annotations
import html
import re
from .extended_review import Evidence, extract_facts, prose

METHOD = 'legacy_52e4d575'
VERSION = 'legacy_emoji_20261007_v1'

def selected(row):
    return row.get('configuration', {}).get('display_version') == VERSION

def cache_key(row):
    cfg = row.get('configuration') or {}
    if cfg.get('methodology_version') != METHOD:
        raise ValueError('Версия методики не соответствует оформлению.')
    return ':'.join([METHOD, VERSION, str(row['call_id']), str(row['id']), row.get('transcript_sha256') or 'unknown'])

def build(row):
    from . import render
    primary = row.get('primary_analysis') or {}
    evidence = Evidence(row)
    detailed = primary.get('detailed_report') or {}
    if not isinstance(detailed, dict): detailed = {}
    manager = detailed.get('for_manager') or {}
    rows = (primary.get('analysis') or {}).get('rows') or []
    brief = primary.get('short_report') or {}

    def supported(text):
        text = prose(text)
        if not text or re.search(r'"\w+"\s*:', text): return ''
        for a, b in re.findall(r'«([^»]+)»|"([^"\n]+)"', text):
            if not evidence.locate(a or b): return ''
        # No model-introduced dates, times, amounts or percentages.
        if any(n not in row.get('transcript', '') for n in re.findall(r'\d+(?:[.,:]\d+)*', text)):
            return ''
        return text

    good, weak = [], []
    def add(target, item, text, quote, advice='', why='', reaction=''):
        found = evidence.locate(quote)
        if not found: return
        turn, actual = found[0]
        previous = next((v for v in target if v['quote']==actual),None)
        if previous is not None:
            if not previous['key']:previous['key']=item.get('key')
            return
        client_reply = next((actual for turn,actual in evidence.locate(reaction) if turn.speaker=='Клиент'), '')
        target.append({'key': item.get('key'), 'text': supported(text),
                       'quote': actual, 'speaker': turn.speaker,
                       'why': supported(why), 'advice': prose(advice), 'reaction':client_reply})

    for item in manager.get('did_well') or []:
        add(good, item, item.get('what'), item.get('quote'), why=item.get('why'))
    for item in manager.get('moments') or []:
        add(weak, item, '', item.get('quote'), item.get('say_instead'), item.get('why'), item.get('reaction'))
    for field, target in [('good', good), ('weak', weak)]:
        for item in brief.get(field) or []:
            matches = evidence.locate(item.get('text'))
            if not matches: continue
            actual = matches[0][1]
            explanation = render.observation_text(item.get('text'), actual)
            explanation = explanation[:1].upper() + explanation[1:]
            add(target, item, explanation, actual, item.get('should_say'))
    for item in sorted(rows, key=lambda x: -int(x.get('weight') or 0)):
        if not item.get('applicable', True) or item.get('key') == 'talk_share': continue
        target = good if item.get('passed') else weak
        title = prose(item.get('title')) or 'Критерий прежней методики'
        if not item.get('passed'):title='Не подтверждено: '+title
        add(target, item, title, item.get('evidence'))
    shared = {v['quote'] for v in good} & {v['quote'] for v in weak}
    good = [v for v in good if v['quote'] not in shared]
    weak = [v for v in weak if v['quote'] not in shared]
    # These are source facts, not new grading criteria. Never expose inferred
    # production/budget context, profile selection or cross-method adjudication.
    allowed = {'materials_agreed','materials_deadline','channel','agreed_callback','callback_proposal'}
    facts = {v.kind: v for v in extract_facts(evidence) if v.kind in allowed}
    agreements = []
    if 'materials_agreed' in facts:
        channel = facts['channel'].value if 'channel' in facts else 'канал нужно уточнить'
        deadline = facts['materials_deadline'].value if 'materials_deadline' in facts else 'не согласован'
        agreements.append(f'Материалы: отправляет менеджер; {channel}; срок отправки — {deadline}.')
    if 'agreed_callback' in facts:
        agreements.append('Повторный контакт: ' + facts['agreed_callback'].value + '.')
    elif 'materials_agreed' in facts or 'callback_proposal' in facts:
        agreements.append(('Срок перезвона не подтверждён в доступном фрагменте.' if not evidence.complete
                           else 'Дата и согласие на перезвон не согласованы.'))
    outcome = supported(brief.get('result'))
    if agreements:
        outcome = ('Согласована отправка материалов.' if 'materials_agreed' in facts else '')
        if 'agreed_callback' in facts: outcome += ' Согласован повторный контакт.'
        if 'materials_agreed' in facts and 'agreed_callback' not in facts:
            outcome += ' Дата следующего разговора остаётся открытой.'
    outcome = outcome.strip() or 'Подтверждённый результат по доступной записи не установлен.'
    notes = []
    if not evidence.complete: notes.append('Полнота записи не подтверждена; выводы относятся к доступному тексту.')
    if any(v.get('key') == 'talk_share' and v.get('applicable', True) for v in rows):
        notes.append('Старая шкала включает баланс речи: число реплик не измеряет время. Балл сохранён без пересчёта.')
    if shared: notes.append('Противоречивые наблюдения исключены из текста; сохранённый балл не изменён.')
    if not detailed: notes.append('Доступны сохранённые критерии и краткий итог; подробных пояснений меньше.')
    if row.get('configuration', {}).get('original_source_known') is False:
        notes.append('Настройки первоначальной оценки сохранились неполностью.')
    actions = []
    if not facts:
        actions = list(dict.fromkeys('Спросить: «'+v['advice']+'»' for v in weak if v['advice']))
    if 'materials_agreed' in facts:
        actions.insert(0, 'Отправить обещанные материалы; факт отправки нужно проверить отдельно.')
    if 'materials_agreed' in facts and 'agreed_callback' not in facts:
        actions.append('Уточнить удобный срок продолжения и получить согласие клиента.')
    return {'outcome':outcome,'good':good,'weak':weak,'agreements':agreements,
            'actions':list(dict.fromkeys(actions)), 'notes':notes, 'evidence':evidence,
            'practice':prose(manager.get('practice')),
            'rows':[v for v in rows if v.get('applicable', True)]}

def display(row, *, detailed=False):
    from . import render
    model = build(row)
    blocks = []
    def section(title, lines):
        if lines: blocks.append((title, '\n'.join(lines)))
    # Keep the saved legacy score and verdict, even if another method offers
    # an apparently better score. No auxiliary method is read above.
    primary = row.get('primary_analysis') or {}
    critical = set((row.get('configuration', {}).get('verdict') or {}).get('critical') or [])
    score = primary.get('score')
    insufficient = score is None or (primary.get('analysis') or {}).get('call_cut_short') or not row.get('transcript','').strip()
    section('🎯 Итог разговора' if detailed else '🎯 Результат', [model['outcome']])
    applicable = model['rows']
    passed = sum(bool(r.get('passed')) for r in applicable)
    explanation = (f'Выполнено {passed} из {len(applicable)} применимых критериев.' if applicable else 'Подробные критерии не сохранены.')
    quality = [render.verdict(row), 'Данных недостаточно для оценки.' if insufficient else f'{score}/100. {explanation}' ]
    quality.append('Балл отражает работу по прежней шкале; коммерческий результат оценивается отдельно.')
    if detailed and not insufficient:
        for item in applicable:
            title = ('Баланс участия — время речи не проверено' if item.get('key') == 'talk_share'
                     else prose(item.get('title')) or 'Критерий прежней методики')
            state = 'выполнено' if item.get('passed') else 'не подтверждено сохранённой проверкой'
            quality.append(f"• {title}: {state}; вес {item.get('weight', '?')}.")
    section('📊 Оценка по прежней методике' if detailed else '📊 Оценка', quality)
    for field, title in [('good','✅ Сильные стороны' if detailed else '✅ Что получилось'),
                         ('weak','⚠️ Ошибки и точки улучшения' if detailed else '⚠️ Что улучшить')]:
        lines=[]
        for item in model[field][:3 if detailed else 2]:
            icon = ('🚨 ' if field=='weak' and primary.get('level')=='❌' and item['key'] in critical else '• ')
            lines.append(icon+(item['text'] or ('Подтверждённое действие.' if field=='good' else 'Точка улучшения по сохранённому разбору.')))
            if len(item['quote']) <= 240: lines.append(item['speaker']+': «'+item['quote']+'»')
            if detailed and item['why']: lines.append(item['why'])
            if field=='weak' and item['advice']: lines.append('Пример: «'+item['advice']+'»')
        if field=='good' and not lines: lines=['В записи недостаточно подтверждений для отдельной сильной стороны.']
        if field=='weak' and not lines:
            lines=['Данных недостаточно, чтобы установить ошибки.' if insufficient or not model['evidence'].complete
                   else 'В сохранённых подтверждённых наблюдениях ошибок не найдено.']
        section(title,lines)
    section('🤝 Договорённости' if detailed else '📌 Следующий шаг', model['agreements']+([] if detailed else ['Рекомендуем: '+a for a in model['actions'][:2]]))
    if detailed:
        objection=next((v for v in model['weak'] if v['reaction'] and v['why'] and v['speaker']=='Менеджер'), None)
        if objection:
            section('💬 Возражение и ответ', ['Клиент: «'+objection['reaction']+'»',
                'Менеджер: «'+objection['quote']+'»',objection['why']]+
                (['Пример ответа: «'+objection['advice']+'»'] if objection['advice'] else []))
        section('📌 Что сделать дальше',['Рекомендуем: '+a for a in model['actions'][:3]])
        practice=model['practice'] or ('Отработать вопрос: «Когда вам удобно обсудить материалы?», дождаться ответа и подтвердить время.' if model['agreements'] else '')
        section('🎯 Что потренировать',[practice] if practice else [])
    section('ℹ️ Ограничения',[' '.join(model['notes'])] if model['notes'] else [])
    heading='📞 Расширенный разбор' if detailed else '📞 Разбор звонка'
    meta=render.header(row).removeprefix('📞 ')
    meta=meta.replace('\n','\n🕒 ',1)
    raw=heading+'\n👤 '+meta+'\n\n'+'\n\n'.join(t+'\n'+b for t,b in blocks)
    if detailed:return render.redact_phone(raw,row)
    def html_text(parts):
        return '<b>'+heading+'</b>\n👤 '+html.escape(meta)+'\n\n'+'\n\n'.join('<b>'+t+'</b>\n'+html.escape(render.redact_phone(b,row)) for t,b in parts)
    text=html_text(blocks)
    # Remove whole secondary observations/sections, never cut a quote/sentence.
    while render.units(text)>4000:
        candidates=[(len(b),i) for i,(t,b) in enumerate(blocks) if t.startswith(('✅','⚠️','ℹ️'))]
        if not candidates:raise ValueError('Короткий разбор слишком длинный.')
        _,index=max(candidates);blocks.pop(index);text=html_text(blocks)
    return text
