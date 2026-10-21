"""
Голосовой оператор: анкета (как в кабинете Соловья) и системный промпт из неё.

Анкета лежит в voice/operators/<id>.toml: кто звонит, приветствие, план
разговора, правила, факты и поля, которые робот собирает. Чтобы поменять
поведение робота, правят анкету, код не трогают.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

OPERATORS_DIR = Path(__file__).parent / "operators"
# Операторы, заведённые в CRM: лежат рядом с данными робота, CRM пишет, робот читает перед каждым звонком
EXTRA_DIR = Path(os.getenv("VOICE_OPERATORS_EXTRA") or "/opt/callbot-voice-data/operators")
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
MAX_ITEMS = 10  # как у Соловья: план, правила и факты до 10 пунктов

OUTCOMES = {
    "interested": "подходит по всем пунктам плана и согласился на звонок специалиста",
    "callback": "просит перезвонить позже или разговор прервался до конца анкеты",
    "not_relevant": "отказался или не подходит по одному из пунктов плана",
    "ndz": "живого разговора не было: автоответчик, голосовое меню, не тот человек",
    "do_not_call": "просит больше не звонить или грубит: номер в стоп-лист",
}


class OperatorError(ValueError):
    """Анкета оператора заполнена неверно."""


@dataclass
class Operator:
    id: str
    name: str
    company: str
    greeting: str
    farewell: str
    goal: str
    lead_source: str
    plan: list[str]
    rules: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    gender: str = "female"
    voice: str = "alena"
    voice_role: str = ""
    voice_speed: float = 1.0
    transfer_to: str = ""
    # быстрые ответы без модели: после вопроса робота (after) на ожидаемый короткий ответ (answers) — reply
    quick: list[dict] = field(default_factory=list)
    owner: str = ""  # чей оператор в CRM: "" — наш (директор), иначе лист клиента

    def validate(self) -> None:
        for key in ("name", "company", "greeting", "farewell", "goal"):
            if not str(getattr(self, key)).strip():
                raise OperatorError(f"{self.id}: не заполнено поле {key}")
        if not self.plan:
            raise OperatorError(f"{self.id}: пустой план разговора")
        for key in ("plan", "rules", "facts"):
            items = getattr(self, key)
            if len(items) > MAX_ITEMS:
                raise OperatorError(f"{self.id}: в блоке {key} {len(items)} пунктов, можно не больше {MAX_ITEMS}")
            if any(not str(item).strip() for item in items):
                raise OperatorError(f"{self.id}: пустой пункт в блоке {key}")
        if not 0.5 <= float(self.voice_speed) <= 2.0:
            raise OperatorError(f"{self.id}: voice_speed от 0.5 до 2.0")
        if self.gender not in ("female", "male"):
            raise OperatorError(f"{self.id}: gender должен быть female или male")
        for item in self.quick:
            if not (str(item.get("after", "")).strip() and item.get("answers") and str(item.get("reply", "")).strip()):
                raise OperatorError(f"{self.id}: в быстром ответе нужны after, answers и reply")
        for key in self.fields:
            if not key.isidentifier():
                raise OperatorError(f"{self.id}: ключ поля {key!r} должен быть латиницей без пробелов")

    def system_prompt(self) -> str:
        return compile_prompt(self)


def find(operator_id: str) -> Path | None:
    """Файл анкеты: сначала заведённые в CRM, потом встроенные."""
    if not ID_RE.fullmatch(operator_id or ""):
        return None
    for directory in (EXTRA_DIR, OPERATORS_DIR):
        path = directory / f"{operator_id}.toml"
        if path.is_file():
            return path
    return None


def all_ids() -> list[str]:
    ids = {p.stem for d in (OPERATORS_DIR, EXTRA_DIR) if d.is_dir() for p in d.glob("*.toml")}
    return sorted(i for i in ids if ID_RE.fullmatch(i))


def builtin(operator_id: str) -> bool:
    return (OPERATORS_DIR / f"{operator_id}.toml").is_file() and not (EXTRA_DIR / f"{operator_id}.toml").is_file()


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # строка JSON — это и строка TOML
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"нельзя записать {type(value).__name__} в анкету")


def dump(data: dict) -> str:
    """Анкета в TOML. Поддерживаем то, что есть в анкете: строки, числа, списки строк, fields и quick."""
    lines, tables = [], []
    for key, value in data.items():
        if key == "fields":
            tables.append("[fields]\n" + "".join(f"{k} = {_toml_value(str(v))}\n" for k, v in value.items()))
        elif key == "quick":
            for item in value:
                tables.append("[[quick]]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in item.items()))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n\n" + "\n".join(tables)


def from_dict(operator_id: str, data: dict) -> Operator:
    known = set(Operator.__dataclass_fields__) - {"id"}
    unknown = set(data) - known
    if unknown:
        raise OperatorError(f"{operator_id}: неизвестные поля {sorted(unknown)}")
    try:
        operator = Operator(id=operator_id, **data)
    except TypeError as e:
        raise OperatorError(f"{operator_id}: {e}") from None
    operator.validate()
    return operator


def load(operator_id: str, directory: Path | None = None) -> Operator:
    if directory is None:
        path = find(operator_id) or OPERATORS_DIR / f"{operator_id}.toml"
    else:
        path = directory / f"{operator_id}.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OperatorError(f"нет анкеты {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise OperatorError(f"{path.name}: ошибка в TOML: {e}") from None
    return from_dict(operator_id, data)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def compile_prompt(op: Operator) -> str:
    me = "в женском роде («поняла», «записала»)" if op.gender == "female" else "в мужском роде («понял», «записал»)"
    said = "произнесла" if op.gender == "female" else "произнёс"
    outcomes = "\n".join(f"- {key}: {text}" for key, text in OUTCOMES.items())
    fields = "\n".join(f"- {key}: {text}" for key, text in op.fields.items()) or "- нет"
    parts = [
        f"Ты {op.name}, голосовой помощник ({op.company}). Ты звонишь по телефону человеку, который {op.lead_source}. "
        f"Цель звонка: {op.goal}. Говоришь о себе {me}.",
        "Как ты говоришь:\n"
        "- Это живой телефонный разговор: каждое твоё слово сразу озвучивает синтезатор. Пиши только то, что произносится вслух: "
        "без списков, скобок, кавычек-ёлочек, эмодзи, ремарок и латиницы. Числа и суммы пиши словами.\n"
        "- Одна реплика: одно-два коротких предложения, не длиннее двадцати слов, и не больше одного вопроса. Не рассказывай то, о чём не спросили. Обычная разговорная речь, на «вы».\n"
        "- Слушай, что уже сказал клиент. Если он ответил на будущий пункт плана заранее, не переспрашивай: "
        "подтверди и иди дальше. Не задавай вопросов, ответ на которые уже очевиден из сказанного.\n"
        "- Говори как живой спокойный менеджер, а не как автоответчик или ведущая: ровный деловой тон, простые слова, без канцелярита. "
        "Не радуйся и не восторгайся: никаких восклицательных знаков, «отлично», «замечательно», «прекрасно», «супер». "
        "Уместны короткие живые связки: «так», «ага», «угу».\n"
        "- Начинай реплику с короткого повтора главного из ответа клиента, как живой менеджер, который записывает: "
        "«Сыктывкар, ага.», «Плёнка, хорошо.», «Двести тридцать, поняла.», «Григорий, очень приятно.». "
        "Если повторять нечего (клиент сказал только «да» или «нет»), начни с «Угу» или «Ага», и не каждый раз. "
        "Не начинай реплики с «Хорошо» и «Поняла» раз за разом.\n"
        "- Вопросы задавай коротко и прямо, как в живом разговоре: «А по бюджету на какую сумму ориентируетесь?», «Как к вам обращаться?». "
        "Не начинай вопрос с «скажите, пожалуйста» или «подскажите, пожалуйста».\n"
        "- Текст клиента приходит из распознавания речи и может быть с ошибками. Если ответ непонятен или оборван, переспроси коротко, "
        "как человек, который не расслышал: «Ещё раз, простите?», «Не расслышала, повторите?». Если не уверена, что поняла верно, "
        "переспроси с догадкой: «Сыктывкар, правильно?».\n"
        "- Цифры, суммы и время повторяй за клиентом, как будто записываешь: «Двести тридцать… так.», «Завтра утром, записала.».\n"
        "- Ты собираешь данные и передаёшь их специалисту, сама расчётов не делаешь. Если клиент спрашивает то, чего нет в фактах, "
        "ответь по-человечески: «Не подскажу, у меня под рукой этой информации нет. Специалист точно скажет.»\n"
        "- В конце скажи, что передаёшь данные специалисту и он позвонит: «Всё, передаю специалисту, ожидайте звонка. Всего доброго.»\n"
        "- На вопрос клиента отвечай по существу из фактов ниже, затем мягко вернись к плану. Если ответа в фактах нет, "
        "не выдумывай: скажи, что это точно подскажет специалист.\n"
        "- Не дави и не спорь. После отказа можно один раз мягко уточнить, после второго отказа вежливо попрощайся.\n"
        "- Если в тексте клиента только «(молчание)», спроси, слышит ли он тебя. После второго молчания заверши звонок с итогом ndz.",
        f"План разговора, по порядку:\n{_numbered(op.plan)}",
    ]
    if op.rules:
        parts.append(f"Правила:\n{_numbered(op.rules)}")
    if op.facts:
        parts.append(f"Факты, на которые можно опираться в ответах (больше ничего о компании не утверждай):\n{_numbered(op.facts)}")
    parts.append(
        "Как заканчивать звонок. Звонок заканчивается только вызовом инструмента:\n"
        "- finish_call: итог звонка и собранные данные. В том же ответе перед вызовом произнеси короткое прощание "
        "(для interested повтори, когда позвонит специалист). Для ndz прощание не нужно.\n"
        + ("- transfer_to_specialist: клиент подходит и хочет поговорить с человеком прямо сейчас. "
           "Перед вызовом скажи, что сейчас соединишь со специалистом.\n" if op.transfer_to else "")
        + f"Итоги:\n{outcomes}\n"
        f"Поля, которые нужно собрать (что клиент не сказал, оставь пустым, не придумывай):\n{fields}"
    )
    parts.append(
        f"Первую реплику ты уже {said}: «{op.greeting}». Дальше продолжай разговор с ответа клиента.\n"
        "When you use a tool, you may say a brief sentence first. If no tool can express what the user asked for, "
        "say so instead of guessing. Do not include internal or system XML tags in your response."
    )
    return "\n\n".join(parts)
