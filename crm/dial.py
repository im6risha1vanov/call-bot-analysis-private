"""Звонок клиенту из CRM через Mango.

Mango сначала звонит на добавочный менеджера (софтфон Mango), а когда он
снимет трубку, набирает клиента. Звонок идёт с номера компании, пишется и
попадает в разбор, как обычный. Добавочный берём из сессии менеджера, а не из
запроса страницы.
"""
from __future__ import annotations

import logging
import time

import mango_client
from crypto_util import decrypt

log = logging.getLogger(__name__)

MIN_GAP_SEC = 8
ACCEPTED = 1000
_last_dial: dict[tuple[int, str], float] = {}


class DialError(ValueError):
    pass


def normalize(number) -> str | None:
    """Российский номер в виде 7XXXXXXXXXX или None.

    Мобильные (9), городские (3, 4, 8) и 8-800. Платные 8-803 и 8-809 не
    набираем: звонок идёт за счёт компании."""
    digits = ''.join(ch for ch in str(number or '') if ch.isdigit())
    if len(digits) == 10:
        digits = '7' + digits
    elif len(digits) == 11 and digits[0] == '8':
        digits = '7' + digits[1:]
    if len(digits) != 11 or digits[0] != '7' or digits[1] not in '3489':
        return None
    if digits.startswith(('7803', '7809')):
        return None
    return digits


async def dial(pool, client_id: int, extension: str | None, number, telegram_user_id: int | None = None) -> dict:
    ext = str(extension or '')
    if not ext.isdigit():
        raise DialError('Звонить можно только с добавочного в Mango, а у этого входа его нет.')
    target = normalize(number)
    if target is None:
        raise DialError('Не получилось разобрать номер. Нужен российский номер из 11 цифр.')
    now = time.monotonic()
    key = (client_id, ext)
    if now - _last_dial.get(key, 0) < MIN_GAP_SEC:
        raise DialError('Звонок уже запущен. Подождите несколько секунд.')
    _last_dial[key] = now
    client = await pool.fetchrow('SELECT vpbx_api_key_enc, vpbx_api_salt_enc FROM clients WHERE id=$1', client_id)
    command_id = f'crm-{ext}-{int(time.time() * 1000)}'
    code = None
    try:
        res = await mango_client.callback(decrypt(client['vpbx_api_key_enc']), decrypt(client['vpbx_api_salt_enc']),
                                          ext, target, command_id)
        code = res.get('result')
    except Exception:
        log.exception('Mango не ответил на команду звонка, доб.=%s', ext)
    dial_id = await pool.fetchval(
        """INSERT INTO crm_dials(client_id,extension,telegram_user_id,number,command_id,result)
           VALUES($1,$2,$3,$4,$5,$6) RETURNING id""", client_id, ext, telegram_user_id, target, command_id, code)
    log.info('звонок из CRM: доб.=%s номер=%s… код=%s', ext, target[:4], code)
    if code != ACCEPTED:
        _last_dial.pop(key, None)
        raise DialError(f'Mango не принял звонок (код {code or "нет ответа"}). Позвоните вручную.')
    return {'dial_id': dial_id, 'number': target}
