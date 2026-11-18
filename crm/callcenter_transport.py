"""CRM request options live outside the byte-pinned original analysis package."""
from __future__ import annotations

import logging

import httpx


async def request(system: str, user: str, max_tokens: int, *,
                  reasoning_effort: str | None = None, json_object: bool = False):
    import analysis

    body = {
        'model': analysis.MODEL,
        'instructions': system,
        'input': user,
        'max_output_tokens': max_tokens,
        'reasoning': {'effort': reasoning_effort or analysis.REASONING_EFFORT},
    }
    if json_object:
        body['text'] = {'format': {'type': 'json_object'}}
    async with httpx.AsyncClient(timeout=analysis.TIMEOUT) as client:
        response = await client.post(
            analysis.CVC_BASE_URL + '/responses',
            headers={'Authorization': 'Bearer ' + analysis.CVC_KEY}, json=body,
        )
    if response.status_code >= 300:
        raise RuntimeError(f'Astra call-center HTTP {response.status_code}')
    data = response.json()
    text = (data.get('output_text') or '').strip()
    if not text:
        text = ''.join(
            part.get('text', '')
            for item in data.get('output', []) if item.get('type') == 'message'
            for part in item.get('content', []) if part.get('type') == 'output_text'
        ).strip()
    usage = data.get('usage') or {}
    cost = analysis._cost(usage)
    logging.info('Call-center tokens input=%s output=%s cost=%.0f',
                 usage.get('input_tokens', 0), usage.get('output_tokens', 0), cost)
    return analysis._parse(text), cost
