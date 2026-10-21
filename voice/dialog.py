"""
Диалог голосового оператора: реплики клиента на входе, реплики робота на выходе.

Модель отвечает потоком, чтобы синтез речи мог начать озвучку с первого
предложения. Звонок заканчивается только инструментом (finish_call или
transfer_to_specialist), поэтому итог всегда структурированный и сразу ложится
в таблицу. Какая модель отвечает, задаёт voice/llm.py (VOICE_LLM, VOICE_MODEL).
"""

from __future__ import annotations

import asyncio
import re
import logging
import time
from dataclasses import dataclass, field

from voice import presets
from voice.llm import BadToolJSON, TextSink, make_backend
from voice.operator import OUTCOMES, Operator

log = logging.getLogger("voice.dialog")

SILENCE = "(молчание)"

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
                "input_schema": {
                "type": "object",
                # описания полей уже есть в finish_call: здесь только имена, инструменты уходят модели в каждом запросе
                "properties": {"comment": comment, "facts": {**facts, "properties": {key: {"type": "string"} for key in op.fields}}},
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


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", text.lower().replace("ё", "е")).split())


class Dialog:
    def __init__(self, operator: Operator, backend=None):
        self.op = operator
        self.backend = backend or make_backend(model=presets.llm_model())  # модель выбранного набора, иначе из .env
        self.model = self.backend.model
        self.system = operator.system_prompt()
        self.tools = build_tools(operator)
        self.messages: list[dict] = []
        self.transcript: list[tuple[str, str]] = []  # ("client" | "robot", текст)
        self.result: CallResult | None = None
        self.cost = 0.0
        self.cost_unit = self.backend.unit
        self.started = time.monotonic()

    @property
    def finished(self) -> bool:
        return self.result is not None

    def greet(self, first_words: str = "Алло") -> str:
        """Робот ждёт «Алло» и говорит приветствие из анкеты (без модели, сразу)."""
        self._add_client(first_words or SILENCE)
        self._add_robot(self.op.greeting)
        return self.op.greeting

    def _add_client(self, text: str) -> None:
        text = text.strip() or SILENCE
        self.transcript.append(("client", text))
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"] += " " + text  # клиент перебил до того, как робот что-то сказал
        else:
            self.messages.append({"role": "user", "content": text})

    def _add_robot(self, text: str) -> None:
        self.transcript.append(("robot", text))
        self.messages.append({"role": "assistant", "content": text})

    def _finish(self, result: CallResult, spoken: str) -> Turn:
        self.result = result
        text = spoken.strip()
        if text or result.outcome == "ndz":
            return Turn(text, result)
        self.transcript.append(("robot", self.op.farewell))
        return Turn(self.op.farewell, result, fallback=True)

    def quick_reply(self, client_text: str) -> str | None:
        """Ожидаемый короткий ответ на вопрос робота («да», «актуально»): заготовка из анкеты, без модели.
        Модель увидит этот обмен в истории и продолжит с него."""
        if self.finished or not self.messages or self.messages[-1]["role"] != "assistant":
            return None
        asked = _norm(self.messages[-1]["content"])
        answer = _norm(client_text)
        for item in self.op.quick:
            if _norm(item["after"]) in asked and answer in {_norm(a) for a in item["answers"]}:
                self._add_client(client_text)
                self._add_robot(item["reply"])
                return item["reply"]
        return None

    def undo_quick(self, reply: str) -> bool:
        """Клиент продолжил говорить поверх быстрого ответа: убираем его из истории, продолжение клиента
        приклеится к его прошлой реплике («да» + «актуален, но я за рулём»), и ответит уже модель."""
        if self.messages and self.messages[-1] == {"role": "assistant", "content": reply}:
            self.messages.pop()
            if self.transcript and self.transcript[-1] == ("robot", reply):
                self.transcript.pop()
            return True
        return False

    def undo_robot(self) -> bool:
        """Клиент договаривал свою фразу, а робот уже ответил на её начало: ответ убираем из истории,
        продолжение клиента приклеится к началу, и модель ответит на фразу целиком."""
        if self.finished or len(self.messages) < 2 or self.messages[-1]["role"] != "assistant" \
                or self.messages[-2]["role"] != "user":
            return False
        text = self.messages.pop()["content"]
        if self.transcript and self.transcript[-1] == ("robot", text):
            self.transcript.pop()
        return True

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

            async def sink(chunk: str) -> None:
                nonlocal first_ms
                if not chunk:
                    return
                if first_ms is None:
                    first_ms = int((time.monotonic() - started) * 1000)
                spoken.append(chunk)
                if on_text:
                    await on_text(chunk)

            try:
                reply = await self.backend.complete(self.system, self.messages, self.tools, sink)
            except asyncio.CancelledError:
                # Клиент перебил: в истории остаётся то, что робот успел начать.
                partial = "".join(spoken).strip()
                if partial:
                    self._add_robot(partial + "…")
                raise
            except BadToolJSON as e:
                log.warning("битый вход инструмента: %s", e)
                said.append("".join(spoken))
                return self._finish(CallResult("callback", "Сбой записи итога, проверить вручную", error=str(e)),
                                    " ".join(said))

            self.cost += reply.cost
            text = ("".join(spoken) or reply.text).strip()
            said.append(text)
            if reply.stop == "refusal":
                if text:
                    self.transcript.append(("robot", text))
                return self._finish(CallResult("callback", "Модель отказалась отвечать, проверить вручную", error="refusal"), text)
            if reply.tool_name is None:
                self._add_robot(text or "…")
                return Turn(text, None, first_ms)
            if text:
                self.transcript.append(("robot", text))
            if reply.stop == "max_tokens":
                return self._finish(CallResult("callback", "Итог обрезан, проверить вручную", error="max_tokens"), text)
            try:
                result = parse_result(reply.tool_name, reply.tool_input, self.op)
            except ValueError as e:
                if attempt == 0:
                    self.messages.append({"role": "assistant", "content": text or "…"})
                    self.messages.append({"role": "user", "content":
                        f"(служебно, клиент этого не слышит: итог записан неверно: {e}. "
                        "Вызови инструмент ещё раз с правильными данными, клиенту ничего не говори.)"})
                    continue
                return self._finish(CallResult("callback", "Сбой записи итога, проверить вручную", error=str(e)),
                                    " ".join(s for s in said if s))
            turn = self._finish(result, " ".join(s for s in said if s))
            turn.first_text_ms = first_ms
            return turn
        raise AssertionError("unreachable")

    async def conclude(self, reason: str = "клиент положил трубку") -> None:
        """Звонок оборвался до finish_call: просим модель записать итог по тому, что успели узнать."""
        if self.finished or not any(who == "client" for who, _ in self.transcript[2:]):
            return
        note = (f"(служебно: {reason}, разговор окончен. Клиенту ничего не говори, "
                "только вызови finish_call с итогом по тому, что успели узнать.)")
        last = self.messages[-1]
        if last["role"] == "assistant":
            messages = self.messages + [{"role": "user", "content": note}]
        else:  # клиент сказал что-то и положил трубку, ответа робота не было
            messages = self.messages[:-1] + [{"role": "user", "content": last["content"] + " " + note}]

        async def ignore(_):
            pass

        try:
            reply = await self.backend.complete(self.system, messages, self.tools, ignore)
            self.cost += reply.cost
            if reply.tool_name:
                result = parse_result(reply.tool_name, reply.tool_input, self.op)
                result.comment = f"{reason.capitalize()}. {result.comment}".strip()
                self.result = result
        except Exception as e:
            log.warning("итог после обрыва не записан: %s", e)

    def duration_s(self) -> int:
        return int(time.monotonic() - self.started)

    def transcript_text(self) -> str:
        names = {"client": "Клиент", "robot": self.op.name}
        return "\n".join(f"{names[who]}: {text}" for who, text in self.transcript)
