"""
Звук для голосового оператора: потоковое распознавание Deepgram и синтез
Yandex SpeechKit в сырой PCM (16 кГц, 16 бит, моно), плюс нарезка ответа
модели на фразы, чтобы озвучка начиналась с первого предложения.

Ключи те же, что у бота: DEEPGRAM_API_KEY и YANDEX_TTS_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx

log = logging.getLogger("voice.speech")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
YANDEX_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
DEEPGRAM_WS = "wss://api.deepgram.com/v1/listen"

# Конец фразы: . ! ? … и пробел после. Длинную фразу без точки режем на запятой.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_LONG = 140


class PhraseSplitter:
    """Копит куски текста от модели и отдаёт готовые к озвучке фразы."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self.buffer += chunk
        out = []
        while True:
            parts = _SENTENCE_END.split(self.buffer, maxsplit=1)
            if len(parts) == 2 and parts[0].strip():
                out.append(parts[0].strip())
                self.buffer = parts[1]
                continue
            if len(self.buffer) > _LONG and ", " in self.buffer[40:]:
                cut = self.buffer.rindex(", ") + 1
                out.append(self.buffer[:cut].strip())
                self.buffer = self.buffer[cut:]
                continue
            return out

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []


# ------------------------------------------------------------------ синтез

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=20.0)
    return _http


def tts_configured() -> bool:
    return bool(os.getenv("YANDEX_TTS_API_KEY", "").strip())


async def synthesize_pcm(text: str, voice: str) -> bytes:
    """Фраза в PCM 16 кГц 16 бит моно (little-endian)."""
    data = {
        "text": text,
        "lang": "ru-RU",
        "voice": voice,
        "format": "lpcm",
        "sampleRateHertz": str(SAMPLE_RATE),
        "speed": os.getenv("VOICE_TTS_SPEED", "1.05"),
    }
    folder_id = os.getenv("YANDEX_TTS_FOLDER_ID", "").strip()
    if folder_id:
        data["folderId"] = folder_id
    r = await _client().post(YANDEX_TTS_URL, headers={"Authorization": f"Api-Key {os.environ['YANDEX_TTS_API_KEY']}"}, data=data)
    if r.status_code != 200:
        raise RuntimeError(f"Yandex SpeechKit {r.status_code}: {r.text[:300]}")
    return r.content


# ------------------------------------------------------------------ распознавание


def deepgram_url() -> str:
    params = {
        "model": os.getenv("VOICE_DG_MODEL", os.getenv("DG_MODEL", "nova-2")),
        "language": os.getenv("DG_LANG", "ru"),
        "encoding": "linear16",
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "interim_results": "true",
        "smart_format": "true",
        "punctuate": "true",
        "endpointing": os.getenv("VOICE_ENDPOINTING_MS", "300"),
        "utterance_end_ms": "1000",
        "vad_events": "true",
    }
    return DEEPGRAM_WS + "?" + urlencode(params)


@dataclass
class Heard:
    text: str
    final: bool  # True: реплика клиента закончена, пора отвечать


class UtteranceCollector:
    """Склеивает сообщения Deepgram в реплики: is_final копим, speech_final или UtteranceEnd закрывают реплику."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def handle(self, message: dict) -> Heard | None:
        kind = message.get("type")
        if kind == "UtteranceEnd":
            return self._close()
        if kind != "Results":
            return None
        alternatives = message.get("channel", {}).get("alternatives") or [{}]
        text = (alternatives[0].get("transcript") or "").strip()
        if message.get("is_final"):
            if text:
                self.parts.append(text)
            if message.get("speech_final"):
                return self._close()
            return Heard(" ".join(self.parts), False) if text else None
        if text:
            return Heard(" ".join(self.parts + [text]), False)
        return None

    def _close(self) -> Heard | None:
        if not self.parts:
            return None
        text, self.parts = " ".join(self.parts), []
        return Heard(text, True)


class DeepgramStream:
    """Потоковое распознавание: send(pcm) на вход, async for heard in stream на выходе."""

    def __init__(self, session) -> None:  # aiohttp.ClientSession
        self.session = session
        self.ws = None
        self.collector = UtteranceCollector()

    async def open(self) -> None:
        self.ws = await self.session.ws_connect(
            deepgram_url(), headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}, heartbeat=10)

    async def send(self, pcm: bytes) -> None:
        if self.ws is not None and not self.ws.closed:
            await self.ws.send_bytes(pcm)

    async def close(self) -> None:
        if self.ws is not None and not self.ws.closed:
            try:
                await self.ws.send_str(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            await self.ws.close()

    async def __aiter__(self) -> AsyncIterator[Heard]:
        import aiohttp
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                heard = self.collector.handle(json.loads(msg.data))
                if heard:
                    yield heard
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break


async def keepalive(stream: DeepgramStream, every: float = 5.0) -> None:
    while stream.ws is not None and not stream.ws.closed:
        await asyncio.sleep(every)
        try:
            await stream.ws.send_str(json.dumps({"type": "KeepAlive"}))
        except Exception:
            return
