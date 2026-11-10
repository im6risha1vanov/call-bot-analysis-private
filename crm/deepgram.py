"""Deepgram для разбора звонков CRM: расшифровка вместе с уверенностью.

Основной клиент бота (`deepgram_client.py`) остаётся без изменений: выкладка
CRM переносит только каталог ``crm/``. Здесь тот же запрос Deepgram и разбор
ответа, но дополнительно возвращается ``confidence``, чтобы неуверенную
расшифровку можно было показать отдельно, а не считать этапы воронки по ней.
"""
from __future__ import annotations

import os

import httpx

DG_MODEL = os.getenv('DG_MODEL', 'nova-2')
DG_LANG = os.getenv('DG_LANG', 'ru')


async def transcribe(path: str) -> tuple[str, float]:
    with open(path, 'rb') as handle:
        content = handle.read()
    text, duration, _confidence = await transcribe_detailed(content)
    return text, duration


async def transcribe_detailed(content: bytes) -> tuple[str, float, float | None]:
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            'https://api.deepgram.com/v1/listen',
            headers={'Authorization': f"Token {os.environ['DEEPGRAM_API_KEY']}", 'Content-Type': 'audio/*'},
            params={'model': DG_MODEL, 'language': DG_LANG, 'diarize': 'true', 'utterances': 'true',
                    'punctuate': 'true', 'smart_format': 'true'},
            content=content,
        )
    if response.status_code >= 400:
        raise RuntimeError(f'Deepgram {response.status_code}: {response.text[:300]}')
    data = response.json()
    duration = data['metadata']['duration']
    utterances = data['results'].get('utterances') or []
    alternative = (data['results'].get('channels') or [{}])[0].get('alternatives') or [{}]
    confidence = alternative[0].get('confidence')
    spoken = [item.get('confidence') for item in utterances if isinstance(item.get('confidence'), (int, float))]
    if spoken:
        confidence = sum(spoken) / len(spoken)

    if not utterances:
        return alternative[0].get('transcript', ''), duration, confidence

    lines, previous = [], None
    for utterance in utterances:
        speaker = utterance.get('speaker', 0)
        if speaker != previous:
            minutes, seconds = divmod(int(utterance.get('start', 0) or 0), 60)
            lines.append(f"\n[{minutes:02d}:{seconds:02d} Спикер {speaker}] {utterance['transcript']}")
            previous = speaker
        else:
            lines[-1] += ' ' + utterance['transcript']
    return '\n'.join(lines).strip(), duration, confidence
