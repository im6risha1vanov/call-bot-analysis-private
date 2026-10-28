"""
Куда пишется итог звонка робота.

1. Всегда: строка в JSONL (VOICE_LOG, по умолчанию voice_calls.jsonl рядом с ботом).
2. Если задан VOICE_SHEET_ID: строка на лист «Робот · Звонки» этой Google-таблицы
   (тот же сервисный аккаунт, GOOGLE_SERVICE_ACCOUNT_FILE). Оттуда звонки робота
   видит AveronCRM, она уже читает таблицы через коннектор Google Sheets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from voice.dialog import CallResult, Dialog

log = logging.getLogger("voice.results")

SHEET_TITLE = "Робот · Звонки"
API = "https://sheets.googleapis.com/v4/spreadsheets/"
MSK = ZoneInfo("Europe/Moscow")

OUTCOME_LABELS = {
    "interested": "Заинтересованный",
    "callback": "Перезвон",
    "not_relevant": "Неактуально",
    "ndz": "НДЗ",
    "do_not_call": "Не звонить",
}
BASE_COLUMNS = ["Дата", "Время", "Оператор", "Номер", "Режим", "Итог", "Комментарий"]
TAIL_COLUMNS = ["Длительность, с", "Стоимость модели", "Разговор"]


def record(dialog: Dialog, *, phone: str = "", mode: str = "браузер", now: datetime | None = None) -> dict:
    result = dialog.result or CallResult("callback", "Звонок оборван до итога", error="no_result")
    now = (now or datetime.now(MSK)).astimezone(MSK)
    return {
        "at": now.isoformat(timespec="seconds"),
        "operator": dialog.op.id,
        "phone": phone,
        "mode": mode,
        "outcome": result.outcome,
        "transfer": result.transfer,
        "comment": result.comment,
        "facts": result.facts,
        "error": result.error,
        "duration_s": dialog.duration_s(),
        "cost": round(dialog.cost, 5),
        "cost_unit": dialog.cost_unit,
        "model": dialog.model,
        "transcript": dialog.transcript,
    }


def sheet_header(fields: list[str]) -> list[str]:
    return BASE_COLUMNS + fields + TAIL_COLUMNS


def sheet_row(rec: dict, fields: list[str], names: dict[str, str]) -> list:
    at = datetime.fromisoformat(rec["at"])
    outcome = OUTCOME_LABELS.get(rec["outcome"], rec["outcome"])
    if rec.get("transfer"):
        outcome += " (перевод)"
    talk = "\n".join(f"{names.get(who, who)}: {text}" for who, text in rec["transcript"])
    return ([at.strftime("%d.%m.%Y"), at.strftime("%H:%M"), rec["operator"], rec["phone"], rec["mode"], outcome, rec["comment"]]
            + [rec["facts"].get(key, "") for key in fields]
            + [rec["duration_s"], f"{rec['cost']} {rec['cost_unit']}", talk[:45000]])


def save_jsonl(rec: dict, path: str | None = None) -> None:
    target = Path(path or os.getenv("VOICE_LOG", "voice_calls.jsonl"))
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"], scopes=["https://www.googleapis.com/auth/spreadsheets"])
    credentials.refresh(Request())
    return credentials.token


async def append_sheet(row: list, header: list[str], *, http: httpx.AsyncClient | None = None,
                       access_token: str | None = None) -> None:
    await append_rows(os.environ["VOICE_SHEET_ID"], SHEET_TITLE, header, [row], http=http, access_token=access_token)


async def append_rows(spreadsheet: str, title: str, header: list[str], rows: list[list], *,
                      http: httpx.AsyncClient | None = None, access_token: str | None = None) -> None:
    """Строки в конец листа title; листа нет — создаётся с заголовком. Чужие листы не трогаются."""
    access_token = access_token or await asyncio.to_thread(_token)
    headers = {"Authorization": "Bearer " + access_token}
    async with (http or httpx.AsyncClient(timeout=30)) as client:
        meta = await client.get(API + spreadsheet, headers=headers, params={"fields": "sheets(properties(title))"})
        meta.raise_for_status()
        titles = {s["properties"]["title"] for s in meta.json().get("sheets", [])}
        if title not in titles:
            r = await client.post(API + spreadsheet + ":batchUpdate", headers=headers, json={"requests": [
                {"addSheet": {"properties": {"title": title, "gridProperties": {"frozenRowCount": 1}}}}]})
            r.raise_for_status()
            r = await client.put(API + spreadsheet + f"/values/'{title}'!A1", headers=headers,
                                 params={"valueInputOption": "RAW"}, json={"values": [header]})
            r.raise_for_status()
        r = await client.post(API + spreadsheet + f"/values/'{title}'!A1:append", headers=headers,
                              params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, json={"values": rows})
        r.raise_for_status()


async def save(dialog: Dialog, *, phone: str = "", mode: str = "браузер", extra: dict | None = None) -> dict:
    rec = record(dialog, phone=phone, mode=mode)
    rec.update(extra or {})
    save_jsonl(rec)
    if os.getenv("VOICE_SHEET_ID", "").strip():
        fields = list(dialog.op.fields)
        try:
            await append_sheet(sheet_row(rec, fields, {"client": "Клиент", "robot": dialog.op.name}), sheet_header(fields))
        except Exception:
            log.exception("не удалось записать звонок робота в таблицу")
    return rec
