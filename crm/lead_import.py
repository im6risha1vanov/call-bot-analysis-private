"""Импорт телефонов в очередь CRM из первого листа Excel или CSV."""
from __future__ import annotations

import base64
import csv

from .dial import normalize
from .robot_store import csv_rows, xlsx_rows
from .store import MAX_LINES

MAX_FILE_BYTES = 280_000
HEADERS = {
    'number': {'телефон', 'тел', 'номер телефона', 'телефон клиента', 'phone', 'mobile', 'номер'},
    'name': {'имя', 'фио', 'контакт', 'имя клиента', 'name', 'contact'},
    'company': {'компания', 'организация', 'фирма', 'название компании', 'company', 'organization'},
    'note': {'комментарий', 'заметка', 'примечание', 'comment', 'note'},
}


def parse_file(filename, payload) -> tuple[list[dict], list[str]]:
    if not isinstance(filename, str) or not filename.casefold().endswith(('.xlsx', '.csv')):
        raise ValueError('Выберите файл Excel (.xlsx) или CSV.')
    if not isinstance(payload, str) or len(payload) > (MAX_FILE_BYTES + 2) // 3 * 4:
        raise ValueError('Файл должен быть не больше 280 КБ.')
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError:
        raise ValueError('Файл не удалось прочитать.') from None
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError('Выберите непустой файл не больше 280 КБ.')
    try:
        if filename.casefold().endswith('.xlsx'):
            rows = xlsx_rows(raw)
        else:
            try:
                decoded = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                decoded = raw.decode('cp1251')
            rows = csv_rows(decoded)
    except (csv.Error, UnicodeDecodeError, RuntimeError, OverflowError, NotImplementedError):
        raise ValueError('Файл не удалось прочитать. Сохраните его в формате .xlsx или .csv.') from None
    return parse_rows(rows)


def parse_rows(rows: list[list[str]]) -> tuple[list[dict], list[str]]:
    numbered = [(i, row) for i, row in enumerate(rows, 1) if any(cell.strip() for cell in row)]
    if not numbered:
        raise ValueError('В файле нет строк с данными.')
    header = [' '.join(cell.casefold().split()) for cell in numbered[0][1]]
    columns = {key: next((i for i, value in enumerate(header) if value in names), None)
               for key, names in HEADERS.items()}
    phone_col = columns['number']
    if phone_col is not None:
        numbered = numbered[1:]
    else:
        matches: dict[int, int] = {}
        for _, row in numbered[:200]:
            for i, cell in enumerate(row):
                if normalize(cell):
                    matches[i] = matches.get(i, 0) + 1
        if not matches:
            raise ValueError('Не нашлось телефонов. Назовите столбец «Телефон».')
        phone_col = max(matches, key=matches.get)
        columns.update(name=phone_col + 1, company=phone_col + 2, note=phone_col + 3)
    if len(numbered) > MAX_LINES:
        raise ValueError(f'В одном файле допускается до {MAX_LINES} строк.')
    found, invalid = [], []
    for line, row in numbered:
        number = normalize(row[phone_col]) if phone_col < len(row) else None
        if not number:
            invalid.append(f'строка {line}')
            continue
        fields = {key: row[index] if index is not None and index < len(row) else None
                  for key, index in columns.items() if key != 'number'}
        found.append({'number': number, **fields})
    if not found:
        raise ValueError('В файле не нашлось корректных номеров телефонов.')
    return found, invalid
