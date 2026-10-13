"""
Диалог голосового оператора: реплики клиента на входе, реплики робота на выходе.

Модель отвечает потоком, чтобы синтез речи мог начать озвучку с первого
предложения. Звонок заканчивается только инструментом (finish_call или
transfer_to_specialist), поэтому итог всегда структурированный и сразу ложится
в таблицу.

Модель по умолчанию claude-haiku-5-5: в живом разговоре важна задержка
ответа. Поменять можно переменной VOICE_MODEL без правки кода.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic

from voice.operator import OUTCOMES, Operator

log = logging.getLogger("voice.dialog")

MODEL = os.getenv("VOICE_MODEL", "claude-haiku-5-5")
EFFORT = os.getenv("VOICE_EFFORT", "low")
MAX_TOKENS = 600
SILENCE = "(молчание)"

# Цены Haiku 5.5 за миллион токенов, для оценки стоимости звонка в логах.
PRICE_IN_MTOK = float(os.getenv("VOICE_PRICE_IN_MTOK", "0.10"))
PRICE_OUT_MTOK = float(os.getenv("VOICE_PRICE_OUT_MTOK", "0.50"))

FINISH = "finish_call"
TRANSFER = "transfer_to_specialist"


@dataclass
class CallResult:
    outcome: str
    comment: str = ""
    facts: dict[str, str] = field(default_factory=dict)
    transfer: bool = False
    error: str | None = None


@dataclass
class Turn:
    text: str
    result: CallResult | None = None
    first_text_ms: int | None = None
    fallback: bool = False  # text не от модели, а прощание из анкеты


def build_tools(op: Operator) -> list[dict]:
    facts = {
        "type": "object",
        "description": "Данные, которые назвал клиент. Чего не было в разговоре, не указывай.",
        "properties": {key: {"type": "string", "description": text} for key, text in op.fields.items()},
        "additionalProperties": False,
    }
    comment = {"type": "string", "description": "Коротко для таблицы, до 160 символов: собранные данные или причина отказа."}
    tools = [{
        "name": FINISH,
        "description": "Завершить звонок и записать итог. Вызывай, когда разговор окончен по плану, правилам или по желанию клиента.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": list(OUTCOMES), "description": "Итог звонка."},
                "comment": comment,
                "facts": facts,
            },
            "required": ["outcome", "comment"],
            "additionalProperties": False,
        },
    }]
    if op.transfer_to:
        tools.append({
            "name": TRANSFER,
            "description": "Перевести клиента на живого специалиста прямо сейчас. Только если клиент подходит и сам хочет говорить с человеком.",
            "eager_input_streaming": True,
            "input_schema": {
                "type": "object",
                "properties": {"comment": comment, "facts": facts},
                "required": ["comment"],
                "additionalProperties": False,
            },
        })
    return tools


def parse_result(name: str, data, op: Operator) -> CallResult:
    """Проверка входа инструмента: при потоковой передаче API его не валидирует."""
    if not isinstance(data, dict):
        raise ValueError("вход инструмента не объект")
    comment = data.get("comment")
    if not isinstance(comment, str) or not comment.strip():
        raise ValueError("нет comment")
    facts = data.get("facts") or {}
    if not isinstance(facts, dict) or any(not isinstance(v, str) for v in facts.values()):
        raise ValueError("facts должен быть объектом со строками")
    facts = {k: v.strip() for k, v in facts.items() if k in op.fields and v.strip()}
    if name == TRANSFER:
        return CallResult("interested", comment.strip()[:300], facts, transfer=True)
    if name != FINISH:
        raise ValueError(f"неизвестный инструмент {name}")
    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError(f"неизвестный итог {outcome!r}")
    return CallResult(outcome, comment.strip()[:300], facts)


TextSink = Callable[[str], Awaitable[None]]


class Dialog:
    def __init__(self, operator: Operator, client: AsyncAnthropic | None = None, model: str = MODEL):
        self.op = operator
        self.client = client or AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.system = [{"type": "text", "text": operator.system_prompt(), "cache_control": {"type": "ephemeral"}}]
        self.tools = build_tools(operator)
        self.messages: list[dict] = []
        self.transcript: list[tuple[str, str]] = []  # ("client" | "robot", текст)
        self.result: CallResult | None = None
        self.cost_usd = 0.0
        self.started = time.monotonic()

    @property
    def finished(self) -> bool:
        return self.result is not None

    def greet(self, first_words: str = "Алло") -> str:
        """Робот ждёт «Алло» и говорит приветствие из анкеты (без модели, сразу)."""
        self._add_client(first_words or SILENCE)
        self.messages.append({"role": "assistant", "content": self.op.greeting})
        self.transcript.append(("robot", self.op.greeting))
        return self.op.greeting

    def _add_client(self, text: str) -> None:
        text = text.strip() or SILENCE
        self.transcript.append(("client", text))
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], str):
            last["content"] += " " + text  # клиент перебил до того, как робот что-то сказал
        else:
            self.messages.append({"role": "user", "content": text})

    def _account(self, usage) -> None:
        if usage is None:
            return
        tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
        self.cost_usd += tokens_in / 1e6 * PRICE_IN_MTOK + (usage.output_tokens or 0) / 1e6 * PRICE_OUT_MTOK

    def _finish(self, result: CallResult, spoken: str) -> Turn:
        self.result = result
        text = spoken.strip()
        if text or result.outcome == "ndz":
            return Turn(text, result)
        self.transcript.append(("robot", self.op.farewell))
        return Turn(self.op.farewell, result, fallback=True)

    async def reply(self, client_text: str, on_text: TextSink | None = None) -> Turn:
        """Ответ робота на реплику клиента. on_text получает куски текста по мере генерации."""
        if self.finished:
            raise RuntimeError("звонок уже завершён")
        self._add_client(client_text)
        started = time.monotonic()
        first_ms = None
        said: list[str] = []
        for attempt in range(2):
            spoken: list[str] = []
            try:
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=self.system,
                    messages=self.messages,
                    tools=self.tools,
                    thinking={"type": "disabled"},
                    output_config={"effort": EFFORT},
                ) as stream:
                    async for chunk in stream.text_stream:
                        if first_ms is None:
                            first_ms = int((time.monotonic() - started) * 1000)
                        spoken.append(chunk)
                        if on_text:
                            await on_text(chunk)
                    msg = await stream.get_final_message()
            except asyncio.CancelledError:
                # Клиент перебил: в истории остаётся то, что робот успел начать.
                partial = "".join(spoken).strip()
                if partial:
                    self.messages.append({"role": "assistant", "content": partial + "…"})
                    self.transcript.append(("robot", partial + "…"))
                raise
            except ValueError as e:  # SDK не смог разобрать JSON инструмента
                log.warning("битый вход инструмента: %s", e)
                said.append("".join(spoken))
                return self._finish(CallResult("callback", "Сбой записи итога, проверить вручную", error=str(e)), " ".join(said))

            self._account(msg.usage)
            text = "".join(spoken)
            said.append(text)
            if text.strip():
                self.transcript.append(("robot", text.strip()))
            tool = next((b for b in msg.content if b.type == "tool_use"), None)

            if msg.stop_reason == "refusal":
                return self._finish(CallResult("callback", "Модель отказалась отвечать, проверить вручную", error="refusal"), text)
            if tool is None:
                self.messages.append({"role": "assistant", "content": text.strip() or "…"})
                return Turn(text.strip(), None, first_ms)
            if msg.stop_reason == "max_tokens":
                return self._finish(CallResult("callback", "Итог обрезан, проверить вручную", error="max_tokens"), text)
            try:
                result = parse_result(tool.name, tool.input, self.op)
            except ValueError as e:
                if attempt == 0:
                    self.messages.append({"role": "assistant", "content": msg.content})
                    self.messages.append({"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": tool.id, "is_error": True,
                        "content": f"Неверный вход инструмента: {e}. Вызови инструмент ещё раз, ничего не говоря клиенту.",
                    }]})
                    continue
                return self._finish(CallResult("callback", "Сбой записи итога, проверить вручную", error=str(e)), " ".join(said))
            turn = self._finish(result, " ".join(s for s in said if s.strip()))
            turn.first_text_ms = first_ms
            return turn
        raise AssertionError("unreachable")

    def duration_s(self) -> int:
        return int(time.monotonic() - self.started)

    def transcript_text(self) -> str:
        names = {"client": "Клиент", "robot": self.op.name}
        return "\n".join(f"{names[who]}: {text}" for who, text in self.transcript)
