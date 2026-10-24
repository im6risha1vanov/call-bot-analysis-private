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
    # быстрые ответы без модели: после вопроса робота (after) на ожидаемый короткий ответ (answers
    # целиком или match/exclude — выражения) — reply; {answer} в reply — слова клиента
    quick: list[dict] = field(default_factory=list)
    owner: str = ""  # чей оператор в CRM: "" — наш (директор), иначе лист клиента
    copy_of: str = ""  # копия встроенного оператора из CRM: быстрые ответы берёт у него, свежие
    # образец живого разговора: модель берёт из него тон и длину реплик (правила одни этого не дают)
    example: str = ""

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
            after = item.get("after", "")
            afters = [after] if isinstance(after, str) else after
            if not (isinstance(afters, list) and afters and all(str(a).strip() for a in afters)
                    and (item.get("answers") or item.get("match")) and str(item.get("reply", "")).strip()):
                raise OperatorError(f"{self.id}: в быстром ответе нужны after, answers или match и reply")
            unless = item.get("unless", [])
            if not isinstance(unless, (str, list)):
                raise OperatorError(f"{self.id}: unless в быстром ответе — строка или список")
            for key in ("match", "exclude", "strip"):
                try:
                    re.compile(item.get(key) or "")
                except re.error as e:
                    raise OperatorError(f"{self.id}: в быстром ответе неверное выражение {key}: {e}") from None
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
    return from_dict(operator_id, _builtin_quick(operator_id, path, data))


def _builtin_quick(operator_id: str, path: Path, data: dict) -> dict:
    """Копия встроенной анкеты из CRM берёт быстрые ответы у встроенной, всегда свежие: новые заготовки
    из репозитория доходят до звонков без пересохранения в CRM. Заготовка срабатывает, только если
    последняя фраза робота содержит её after, поэтому изменённое приветствие или план её не сломают.
    Старые копии без copy_of узнаём по той же компании («кухни на заказ»)."""
    if data.get("quick"):
        return data
    source = data.get("copy_of") or (operator_id if (OPERATORS_DIR / f"{operator_id}.toml").is_file() else "")
    candidates = [OPERATORS_DIR / f"{source}.toml"] if source else sorted(OPERATORS_DIR.glob("*.toml"))
    for own in candidates:
        if own == path or not own.is_file() or not ID_RE.fullmatch(own.stem):
            continue
        try:
            base = tomllib.loads(own.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            continue
        if base.get("quick") and (source or base.get("company") == data.get("company")):
            return {**data, "quick": base["quick"]}
    return data


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def compile_prompt(op: Operator) -> str:
    me = "в женском роде («поняла», «записала»)" if op.gender == "female" else "в мужском роде («понял», «записал»)"
    said = "произнесла" if op.gender == "female" else "произнёс"
    outcomes = "\n".join(f"- {key}: {text}" for key, text in OUTCOMES.items())
    fields = ", ".join(op.fields) or "нет"
    # Промт переписан 21.10 с нуля: вместо длинного списка правил — кто ты, как звучит живой менеджер и образец разговора.
    # По образцу модель лучше держит тон и длину реплики, чем по запретам.
    parts = [
        f"Ты {op.name}, менеджер ({op.company}). Говоришь о себе {me}. Звонишь человеку, который {op.lead_source}. "
        f"Задача звонка: {op.goal}.",
        "Как ты разговариваешь. Ты обычный опытный менеджер на телефоне, который сделал сотни таких звонков: спокойно, коротко, по-простому.\n"
        "- Реплика короткая, как в живом разговоре: обычно одна фраза и один вопрос, до пятнадцати слов. Длиннее только когда объясняешь что-то по делу.\n"
        "- Отвечай на смысл сказанного своими словами, как человек, который слушает. Иногда просто задай следующий вопрос. "
        "Не начинай реплики одинаково и не начинай их со «так», «итак» или «записываю».\n"
        "- Каждый довод говори один раз за звонок. Если уже сказала про материал, замер или цену, второй раз не повторяй: "
        "лучше спроси, что смущает.\n"
        "- На вопрос не по плану (какие ещё есть варианты, сколько стоит, откуда номер) сначала коротко ответь по фактам, "
        "потом вернись к своему вопросу. Ответ на вопрос клиента можно начать с короткого «угу» или «да», но не каждый раз. "
        "Если ответа нет в фактах, честно отдай вопрос специалисту и привяжи к тому, что клиент уже сказал: "
        "«Сроки специалист скажет по вашему проекту, с учётом размеров», и продолжай по плану.\n"
        "- Если клиент поправил свой ответ (сначала «сейчас», потом «через час»), бери последний вариант и подтверди его один раз.\n"
        "- Когда клиент назвал имя, обратись по имени ещё один-два раза за звонок (при вопросе о времени и в конце), не чаще.\n"
        "- Простые слова, на «вы». Без канцелярита, без «скажите, пожалуйста», без восторгов, «отлично» и восклицательных знаков.\n"
        "- Если по смыслу ответ понятен, не переспрашивай, даже если в тексте ошибки распознавания. Если понятно наполовину, повтори услышанное и спроси «…, верно?» («Москва, верно?»). Если совсем непонятно: «Ещё раз, простите?». Одним и тем же переспросом два раза подряд не отвечай.\n"
        "- Если клиент уже ответил на следующий вопрос плана, не задавай его.\n"
        "- Ты собираешь данные и передаёшь их специалисту. Чего нет в фактах, не выдумывай: «Это вам специалист точно скажет.»\n"
        "- Не дави. После отказа можно один раз мягко уточнить, после второго вежливо попрощайся.\n"
        "- Пиши только то, что произносится вслух: без скобок, списков, кавычек-ёлочек и латиницы. Числа словами.\n"
        "- Если в тексте клиента только «(молчание)», спроси, слышно ли тебя. После второго молчания заверши звонок с итогом ndz.",
    ]
    if op.example.strip():
        parts.append("Образец живого разговора. Это пример тона и длины реплик, а не текст для повторения слово в слово:\n"
                     + op.example.strip())
    parts.append(f"План разговора, по порядку:\n{_numbered(op.plan)}")
    if op.rules:
        parts.append(f"Правила:\n{_numbered(op.rules)}")
    if op.facts:
        parts.append(f"Факты, на которые можно опираться в ответах (больше ничего о компании не утверждай):\n{_numbered(op.facts)}")
    parts.append(
        "Как заканчивать звонок. Звонок заканчивается только вызовом инструмента:\n"
        "- finish_call: итог звонка и собранные данные. В том же ответе перед вызовом произнеси короткое прощание "
        "(для interested: если итог уже подведён и клиент согласился, просто попрощайся, иначе повтори, когда позвонит специалист). "
        "Для ndz прощание не нужно.\n"
        + ("- transfer_to_specialist: клиент подходит и хочет поговорить с человеком прямо сейчас. "
           "Перед вызовом скажи, что сейчас соединишь со специалистом.\n" if op.transfer_to else "")
        + f"Итоги:\n{outcomes}\n"
        f"Поля для facts (описания в инструменте finish_call; чего клиент не сказал, не пиши): {fields}"
    )
    parts.append(
        f"Первую реплику ты уже {said}: «{op.greeting}». Без служебных тегов и XML в ответе."
    )
    return "\n\n".join(parts)
