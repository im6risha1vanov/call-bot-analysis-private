"""Encode text cells that spreadsheet applications would treat as formulas."""


def as_text(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value
