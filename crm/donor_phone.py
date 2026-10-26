"""Номера для агента доноров: разбор, формат и оператор по реестру Минцифры.

Реестр (donor_data/phone_registry.json.gz) собран из данных phoneoperator (MIT, LICENSE-phoneoperator
рядом), снимок 2026-10-01: мобильные (9xx) и городские (3xx, 4xx, 8xx) диапазоны. Формат тот же, что в
расширении v2: диапазоны записаны строкой «пропуск,длина,оператор,место;…» в base36.
"""
from __future__ import annotations

import bisect
import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

REGISTRY = Path(__file__).with_name('donor_data') / 'phone_registry.json.gz'
BRANDS = {
    '7740000076': 'МТС', '7812014560': 'МегаФон', '7713076301': 'Билайн', '7743895280': 'T2', '7701725181': 'Yota',
    '7743200179': 'Т-Мобайл', '7736264044': 'СберМобайл', '9725152215': 'Альфа-Мобайл', '9705121882': 'ВТБ Мобайл',
    '6661079603': 'Мотив', '2308210371': 'Win mobile', '7718999159': 'Волна мобайл', '7707049388': 'Ростелеком', '7705017253': 'МТТ',
}
# Операторы виртуальной телефонии и колл-трекинга (юрлица из реестра): такой номер может быть из пула.
VIRTUAL = re.compile(r'МТТ|МАНГО|МСН ТЕЛЕКОМ|СИПНЕТ|КОЛЛКИПЕР|ВЕСТ КОЛЛ|МОБИЛОН|ОНЛАЙН ЛОДЖИК|СИПАУТНЭТ|СИП-ТЕЛЕКОМ|'
                     r'БИЗНЕС КЛАУД|ОНЛАЙНФОН|КОНВЕКС|ТЕЛФИН|ЗАДАРМА', re.I)
PHONE_RE = re.compile(r'(?:\+7|(?:^|(?<=[^\d]))8)[\s \-–(]*\d{3}[\s \-–)]*\d{3}[\s \-–]*\d{2}[\s \-–]*\d{2}(?!\d)')


def norm(value) -> str:
    """Цифры номера в виде 7XXXXXXXXXX; что не похоже на российский номер, вернётся как есть (цифры)."""
    d = re.sub(r'\D', '', str(value or ''))
    if len(d) == 10:
        d = '7' + d
    if len(d) == 11 and d[0] == '8':
        d = '7' + d[1:]
    return d


def valid(n: str) -> bool:
    return bool(re.fullmatch(r'7[3489]\d{9}', n or ''))


def fmt(n: str) -> str:
    if valid(n) and n[1] == '8':  # 8-800 и другие бесплатные
        return f'8 {n[1:4]} {n[4:7]}-{n[7:9]}-{n[9:]}'
    if valid(n):
        return f'+7 {n[1:4]} {n[4:7]}-{n[7:9]}-{n[9:]}'
    return n


def find(text: str) -> list[tuple[str, int]]:
    """Номера в тексте: (номер, позиция)."""
    out = []
    for m in PHONE_RE.finditer(text or ''):
        n = norm(m.group(0))
        if valid(n):
            out.append((n, m.start()))
    return out


def _decode(data: dict, kind: str) -> dict:
    starts, ends, ops, places = [], [], [], []
    prev = -1
    for entry in data['ranges'].split(';'):
        parts = [int(x, 36) if x else 0 for x in entry.split(',')] + [0, 0, 0, 0]
        gap, length, op, place = parts[:4]
        start = prev + 1 + gap
        prev = start + length
        starts.append(start)
        ends.append(prev)
        ops.append(op)
        places.append(place)
    plist = []
    for value in data['places']:
        first, sep, second = value.partition('|')
        plist.append((second, first) if sep else (first, ''))  # (регион, населённый пункт)
    return {'type': kind, 'starts': starts, 'ends': ends, 'ops': ops, 'places': places,
            'operators': data['operators'], 'placelist': plist}


@lru_cache(maxsize=1)
def tables() -> list[dict]:
    with gzip.open(REGISTRY, 'rt', encoding='utf-8') as f:
        raw = json.load(f)
    return [_decode(raw['mobile'], 'mobile'), _decode(raw['landline'], 'landline')]


def operator(n: str) -> dict | None:
    """{'type': mobile|landline, 'op', 'region', 'locality', 'virtual'} или None, если диапазона нет в реестре."""
    if not valid(n):
        return None
    value = int(n[1:])
    for t in tables():
        i = bisect.bisect_left(t['ends'], value)
        if i < len(t['starts']) and t['starts'][i] <= value <= t['ends'][i]:
            inn, name = t['operators'][t['ops'][i]]
            region, locality = t['placelist'][t['places'][i]]
            short = re.sub(r'^(ООО|ПАО|АО|ОАО|ЗАО)\s*', '', name).replace('"', '').replace('«', '').replace('»', '')[:40]
            return {'type': t['type'], 'op': BRANDS.get(inn) or short, 'region': region, 'locality': locality,
                    'virtual': bool(VIRTUAL.search(name))}
    return None
