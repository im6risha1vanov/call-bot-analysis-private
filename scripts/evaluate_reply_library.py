"""Offline held-out replay. Input: JSON array {call_id, transcript, expected_next_action?}.
Never accesses production or calls a model. Results are selection estimates, not outcome claims.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crm.robot_reply_learning import extract, intent_of, build_candidates
from voice import reply_library, speech


def split(call_id):
    return 'test' if int(hashlib.sha256(str(call_id).encode()).hexdigest()[:8], 16) % 5 == 0 else 'train'


def evaluate(calls, profile='generic'):
    ids = [str(c.get('call_id', '')) for c in calls]
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('call_id must be nonempty and unique')
    groups = {'train': [], 'test': []}
    for call in calls:
        status, reason, pairs = extract(call.get('transcript', []))
        groups[split(call['call_id'])].append((call, status, reason, pairs))
    # Observations are simulated as candidates only; approval is not inferred.
    sources = [{'source_type':'offline','source_id':str(call['call_id']),'evidence':pairs}
               for call, status, reason, pairs in groups['train'] if status == 'extracted']
    drafts = build_candidates(sources, profile, guide_enabled=(profile == 'packaging'))
    candidates = [{'id':n+1,'version_id':1,'triggers':candidate['triggers'],
                   'reply':candidate['answer'],'sample_rate':speech.TTS_RATE,
                   'audio_pcm_b64':base64.b64encode(b'\0\0'*480).decode()}
                  for n, candidate in enumerate(drafts) if not candidate.get('fact_needed')]
    candidates = reply_library.prepare(candidates, speech.TTS_RATE)
    counts = Counter(); intents = {}; timestamps = Counter()
    for call, status, reason, pairs in groups['test']:
        counts['test_calls'] += 1
        if status != 'extracted': counts['unusable_calls'] += 1
        for pair in pairs:
            intent = pair.get('intent') or intent_of(pair['question'])
            if intent.startswith('unknown'): intent = 'unknown'
            c = intents.setdefault(intent, Counter())
            c['turns'] += 1; counts['baseline_recorded_next_turn'] += 1
            item, confidence = reply_library.match(candidates, pair['question'], [], '')
            if item: c['candidate_selected'] += 1; counts['candidate_selected'] += 1
            else: c['fallback'] += 1; counts['fallback'] += 1
            if pair.get('repeat_question'): c['observed_reasks'] += 1
            if pair.get('at') is None: timestamps['client_start_missing'] += 1
            else: timestamps['client_start_available'] += 1
            # A start timestamp alone cannot establish end-of-speech latency.
            timestamps['end_of_speech_unavailable'] += 1
    return {'split':'sha256(call_id) mod 5; 0=test', 'train_calls':len(groups['train']),
            'test_calls':len(groups['test']), 'candidate_count':len(candidates), 'fact_needed_blocked':sum(bool(d.get('fact_needed')) for d in drafts),
            'counts':dict(counts), 'by_intent':{k:dict(v) for k,v in intents.items()},
            'timestamps':dict(timestamps), 'appropriate_next_action':'unavailable: no adjudicated turn labels',
            'actual_response_latency':'unavailable: client end-of-speech timestamps not supplied',
            'note':'Training observations simulate triggers; never approved or deployed. Baseline is recorded next response, not model replay. Reasks are observed and not attributed to any simulated response.'}


async def replay():
    from voice.call import CallSession
    from voice.dialog import Dialog
    from voice.operator import load
    class Backend:
        model='offline'; unit=''
    timings=[]
    async def audio(pcm): timings.append(time.perf_counter())
    async def event(data): pass
    dialog=Dialog(load('kitchen'),Backend()); dialog.greet()
    session=CallSession(dialog,audio,event)
    session.approved_replies=reply_library.prepare([{'id':1,'version_id':1,'triggers':['сколько стоит'],
      'reply':'Уточнит специалист.','voice_signature':reply_library.signature(session.voice, 'Уточнит специалист.'), 'sample_rate':speech.TTS_RATE,'audio_pcm_b64':base64.b64encode(b'\0\0'*480).decode()}],speech.TTS_RATE)
    started=time.perf_counter(); await session._respond('сколько стоит'); session._cancel_silence()
    return {'scope':'in-process send_audio callback only; not SIP/network/playback',
            'dispatch_ms': round((timings[0]-started)*1000, 3) if timings else None,
            'end_of_speech_to_audible_ms':'unavailable'}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input',type=Path); ap.add_argument('--profile', choices=['generic','packaging'], default='generic'); ap.add_argument('--output',type=Path); args=ap.parse_args()
    result=evaluate(json.loads(args.input.read_text(encoding='utf-8')), args.profile); result['transport_replay']=asyncio.run(replay())
    raw=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: args.output.write_text(raw+'\n',encoding='utf-8')
    print(raw)
if __name__ == '__main__': main()
