"""
Модели для голосового оператора. Две реализации с одним интерфейсом:

- cvc (по умолчанию): прокси ai.starimg.ru, тот же ключ CVC_API_KEY, на котором
  бот делает разборы. Responses API, ответ потоком (SSE).
- openrouter: OpenRouter, ключ OPENROUTER_API_KEY (тот же, что для голосов el:).
  Chat Completions, ответ потоком; стоимость в долларах приходит в самом ответе.
- yandex: YandexGPT в Яндекс Облаке, российская оплата и серверы, ответ ~1 с.
- anthropic: Claude напрямую, ключ ANTHROPIC_API_KEY.

Выбор: VOICE_LLM=cvc|yandex|openrouter|anthropic. Модель: VOICE_MODEL (для cvc по умолчанию
CVC_MODEL бота, для openrouter anthropic/claude-haiku-5.5, для anthropic claude-haiku-5-5).

История разговора хранится в нейтральном виде: [{"role": "user"|"assistant", "content": str}].
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("voice.llm")

TextSink = Callable[[str], Awaitable[None]]


@dataclass
class ModelReply:
    text: str
    tool_name: str | None = None
    tool_input: object = None
    stop: str = "end"        # end | tool | max_tokens | refusal
    cost: float = 0.0


class BadToolJSON(ValueError):
    """Модель вернула вызов инструмента с неразборчивыми аргументами."""


def neutral_tools(tools: list[dict]) -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]


# ------------------------------------------------------------------ ai.starimg.ru


class CvcBackend:
    unit = "ед."  # биллинг прокси в кредит-токенах, не в долларах (см. analysis.py)

    def __init__(self, model: str | None = None, *, http: httpx.AsyncClient | None = None,
                 base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model or os.getenv("VOICE_MODEL") or os.getenv("CVC_MODEL", "gpt-6-astra")
        self.base_url = (base_url or os.getenv("CVC_BASE_URL", "https://ai.starimg.ru/v1")).rstrip("/")
        self.api_key = api_key or os.environ["CVC_API_KEY"]
        self.effort = os.getenv("VOICE_CVC_EFFORT", "low")
        self.max_output = int(os.getenv("VOICE_CVC_MAX_OUTPUT", "2000"))
        self.multiplier = float(os.getenv("CVC_MODEL_MULTIPLIER", "6.5"))
        self.http = http

    def _cost(self, usage: dict) -> float:
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        fresh = max(usage.get("input_tokens", 0) - cached, 0)
        return (fresh + cached * 0.1 + usage.get("output_tokens", 0)) * self.multiplier

    def body(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        body = {
            "model": self.model,
            "instructions": system,
            "input": [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_output_tokens": self.max_output,
            "reasoning": {"effort": self.effort},
            "stream": True,
        }
        if tools:
            body["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                              "parameters": t["input_schema"]} for t in tools]
            body["tool_choice"] = "auto"
        return body

    async def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: TextSink) -> ModelReply:
        if self.http is None:  # одно соединение на весь процесс: без TLS-рукопожатия на каждой реплике
            self.http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        async with self.http.stream("POST", self.base_url + "/responses", json=self.body(system, messages, tools),
                                     headers={"Authorization": "Bearer " + self.api_key}) as r:
            if r.status_code >= 300:
                raise RuntimeError(f"ai.starimg.ru {r.status_code}: {(await r.aread()).decode(errors='replace')[:500]}")
            if "text/event-stream" not in r.headers.get("content-type", ""):
                data = json.loads(await r.aread())  # прокси ответил целиком, без потока
                reply = self._from_response(data)
                if reply.text:
                    await on_text(reply.text)
                return reply
            streamed = []
            final = None
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                event = json.loads(payload)
                kind = event.get("type", "")
                if kind == "response.output_text.delta":
                    streamed.append(event.get("delta", ""))
                    await on_text(event.get("delta", ""))
                elif kind in ("response.completed", "response.incomplete"):
                    final = event.get("response") or {}
                elif kind in ("response.failed", "error"):
                    raise RuntimeError(f"ai.starimg.ru: {json.dumps(event, ensure_ascii=False)[:500]}")
        if final is None:
            raise RuntimeError("ai.starimg.ru оборвал поток без итога")
        reply = self._from_response(final)
        reply.text = "".join(streamed) or reply.text
        return reply

    def _from_response(self, data: dict) -> ModelReply:
        text = "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for part in item.get("content", []) if part.get("type") == "output_text")
        reply = ModelReply(text, cost=self._cost(data.get("usage") or {}))
        call = next((item for item in data.get("output", []) if item.get("type") == "function_call"), None)
        if data.get("status") == "incomplete":
            reply.stop = "max_tokens"
        if call is not None:
            reply.tool_name = call.get("name")
            try:
                reply.tool_input = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError as e:
                raise BadToolJSON(str(e)) from None
            if reply.stop != "max_tokens":
                reply.stop = "tool"
        return reply


# ------------------------------------------------------------------ Chat Completions: OpenRouter, YandexGPT


class ToolTextGate:
    """Ловит вызов инструмента, который модель написала текстом («finish_call {...}») вместо tool_calls.
    YandexGPT так делает. Такой текст не должен попасть в озвучку: пропускаем всё до имени
    инструмента, а хвост строки, похожий на начало имени, придерживаем до следующего куска."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.buf = ""
        self.tool: str | None = None
        self.args = ""

    def _hold(self, line: str) -> int:
        """Сколько последних символов строки могут оказаться началом имени инструмента."""
        for k in range(min(len(line), max(map(len, self.names), default=0)), 0, -1):
            if any(n.startswith(line[-k:]) for n in self.names):
                return k
        return 0

    def feed(self, chunk: str) -> str:
        if self.tool is not None:
            self.args += chunk
            return ""
        self.buf += chunk
        hits = [(self.buf.find(n), n) for n in self.names if n in self.buf]
        if hits:
            pos, name = min(hits)
            out, self.tool, self.args = self.buf[:pos], name, self.buf[pos + len(name):]
            self.buf = ""
            return out
        line_start = self.buf.rfind("\n") + 1
        keep = self._hold(self.buf[line_start:])
        out, self.buf = self.buf[:len(self.buf) - keep], self.buf[len(self.buf) - keep:]
        return out

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return out

    def call(self) -> tuple[str, object] | None:
        if self.tool is None:
            return None
        raw = self.args
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise BadToolJSON(f"{self.tool}: нет JSON в «{raw[:100]}»")
        try:
            return self.tool, json.loads(raw[start:end + 1])
        except json.JSONDecodeError as e:
            raise BadToolJSON(str(e)) from None


_shared_http: httpx.AsyncClient | None = None


def shared_http() -> httpx.AsyncClient:
    """Одно соединение на процесс: звонки не платят TLS-рукопожатием за первую реплику."""
    global _shared_http
    if _shared_http is None:
        _shared_http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
    return _shared_http


async def warm_up(backend) -> None:
    """Открыть соединение с моделью заранее, пока звучит приветствие."""
    url = getattr(backend, "URL", "")
    if url:
        try:
            await (backend.http or shared_http()).head(url.rsplit("/", 2)[0], timeout=5)
        except Exception:
            pass


class ChatBackend:
    """Ответ потоком через OpenAI-совместимый Chat Completions. Настройки берут подклассы."""
    unit = "$"
    URL = ""

    def __init__(self, model: str, api_key: str, *, http: httpx.AsyncClient | None = None) -> None:
        self.model = model
        self.api_key = api_key
        self.max_output = int(os.getenv("VOICE_MAX_OUTPUT", "600"))
        self.http = http

    def headers(self) -> dict:
        return {"Authorization": "Bearer " + self.api_key}

    def system_message(self, system: str) -> dict:
        return {"role": "system", "content": system}

    def extra(self) -> dict:
        return {}

    def body(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        body = {
            "model": self.model,
            "messages": [self.system_message(system)] + [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_tokens": self.max_output,
            "stream": True,
            **self.extra(),
        }
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["input_schema"]}} for t in tools]
            body["tool_choice"] = "auto"
        return body

    async def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: TextSink) -> ModelReply:
        if self.http is None:
            self.http = shared_http()
        name = type(self).__name__.removesuffix("Backend")
        text, calls, finish, usage = [], {}, None, {}
        gate = ToolTextGate([t["name"] for t in tools])
        async with self.http.stream("POST", self.URL, json=self.body(system, messages, tools), headers=self.headers()) as r:
            if r.status_code >= 300:
                raise RuntimeError(f"{name} {r.status_code}: {(await r.aread()).decode(errors='replace')[:500]}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue  # ": OPENROUTER PROCESSING" и пустые строки
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                event = json.loads(payload)
                if event.get("error"):
                    raise RuntimeError(f"{name}: {json.dumps(event['error'], ensure_ascii=False)[:500]}")
                usage = event.get("usage") or usage
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        said = gate.feed(delta["content"])
                        if said:
                            text.append(said)
                            await on_text(said)
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(call.get("index", 0), {"name": "", "arguments": ""})
                        fn = call.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    finish = choice.get("finish_reason") or finish
        rest = gate.flush()
        if rest:
            text.append(rest)
            await on_text(rest)
        reply = ModelReply("".join(text), cost=self.cost(usage))
        if finish == "length":
            reply.stop = "max_tokens"
        written = None if calls else gate.call()
        if written is not None:
            reply.tool_name, reply.tool_input = written
            if reply.stop != "max_tokens":
                reply.stop = "tool"
        if calls:
            call = calls[min(calls)]
            reply.tool_name = call["name"]
            try:
                reply.tool_input = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError as e:
                raise BadToolJSON(str(e)) from None
            if reply.stop != "max_tokens":
                reply.stop = "tool"
        return reply

    def cost(self, usage: dict) -> float:
        return float(usage.get("cost") or 0)


class OpenRouterBackend(ChatBackend):
    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str | None = None, *, http: httpx.AsyncClient | None = None,
                 api_key: str | None = None) -> None:
        super().__init__(model or os.getenv("VOICE_MODEL", "anthropic/claude-haiku-5.5"),
                         api_key or os.environ["OPENROUTER_API_KEY"], http=http)

    def headers(self) -> dict:
        return {**super().headers(), "X-Title": "callbot-voice"}

    def system_message(self, system: str) -> dict:
        # cache_control: у моделей Claude длинная инструкция кешируется и стоит в 10 раз дешевле
        return {"role": "system", "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}

    def extra(self) -> dict:
        return {"usage": {"include": True}, "reasoning": {"enabled": False}}


class YandexBackend(ChatBackend):
    """YandexGPT в Яндекс Облаке (AI Studio): российская оплата, серверы в России.
    Ключ YANDEX_GPT_API_KEY (или ключ озвучки YANDEX_TTS_API_KEY, если у сервисного аккаунта
    есть роль ai.languageModels.user), каталог YANDEX_FOLDER_ID (или YANDEX_TTS_FOLDER_ID)."""
    URL = "https://llm.api.cloud.yandex.net/v1/chat/completions"
    unit = "ток."

    def __init__(self, model: str | None = None, *, http: httpx.AsyncClient | None = None,
                 api_key: str | None = None, folder: str | None = None) -> None:
        self.folder = (folder or os.getenv("YANDEX_FOLDER_ID") or os.getenv("YANDEX_TTS_FOLDER_ID") or "").strip()
        if not self.folder:
            raise RuntimeError("нет YANDEX_FOLDER_ID: ID каталога из консоли Яндекс Облака (b1g…)")
        name = model or os.getenv("VOICE_MODEL", "yandexgpt-lite/latest")
        if not name.startswith("gpt://"):
            name = f"gpt://{self.folder}/{name if '/' in name else name + '/latest'}"
        super().__init__(name, (api_key or os.getenv("YANDEX_GPT_API_KEY") or os.environ["YANDEX_TTS_API_KEY"]).strip(),
                         http=http)

    def headers(self) -> dict:
        return {**super().headers(), "OpenAI-Project": self.folder}

    def cost(self, usage: dict) -> float:
        return float(usage.get("total_tokens") or 0)  # биллинг Яндекса в токенах, рубли считаются по тарифу модели


# ------------------------------------------------------------------ Anthropic


class AnthropicBackend:
    unit = "$"
    PRICE_IN_MTOK = float(os.getenv("VOICE_PRICE_IN_MTOK", "0.10"))   # Haiku 5.5
    PRICE_OUT_MTOK = float(os.getenv("VOICE_PRICE_OUT_MTOK", "0.50"))

    def __init__(self, model: str | None = None, *, client=None) -> None:
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.client = client
        self.model = model or os.getenv("VOICE_MODEL", "claude-haiku-5-5")
        self.effort = os.getenv("VOICE_EFFORT", "low")

    def _cost(self, usage) -> float:
        if usage is None:
            return 0.0
        tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
        return tokens_in / 1e6 * self.PRICE_IN_MTOK + (usage.output_tokens or 0) / 1e6 * self.PRICE_OUT_MTOK

    async def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: TextSink) -> ModelReply:
        kwargs = dict(
            model=self.model, max_tokens=600, messages=messages,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "disabled"}, output_config={"effort": self.effort},
        )
        if tools:
            kwargs["tools"] = [{**t, "eager_input_streaming": True} for t in tools]
        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    await on_text(chunk)
                msg = await stream.get_final_message()
        except ValueError as e:  # SDK не смог разобрать JSON инструмента
            raise BadToolJSON(str(e)) from None
        text = "".join(b.text for b in msg.content if b.type == "text")
        reply = ModelReply(text, cost=self._cost(msg.usage))
        tool = next((b for b in msg.content if b.type == "tool_use"), None)
        if msg.stop_reason == "refusal":
            reply.stop = "refusal"
        elif tool is not None:
            reply.tool_name, reply.tool_input = tool.name, tool.input
            reply.stop = "max_tokens" if msg.stop_reason == "max_tokens" else "tool"
        return reply


def make_backend(kind: str | None = None, model: str | None = None):
    kind = (kind or os.getenv("VOICE_LLM", "cvc")).strip().lower()
    if kind == "cvc":
        return CvcBackend(model)
    if kind == "openrouter":
        return OpenRouterBackend(model)
    if kind == "yandex":
        return YandexBackend(model)
    if kind == "anthropic":
        return AnthropicBackend(model)
    raise ValueError(f"VOICE_LLM={kind!r}: можно cvc, yandex, openrouter или anthropic")


def describe() -> str:
    """Какая модель отвечает, для страницы теста."""
    kind = os.getenv("VOICE_LLM", "cvc").strip().lower()
    default = {"cvc": os.getenv("CVC_MODEL", "gpt-6-astra"), "openrouter": "anthropic/claude-haiku-5.5",
               "anthropic": "claude-haiku-5-5", "yandex": "yandexgpt-lite/latest"}.get(kind, "?")
    return f"{kind}: {os.getenv('VOICE_MODEL') or default}"
