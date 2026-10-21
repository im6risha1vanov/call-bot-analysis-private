"""Себестоимость звонка робота: CRM сама считает цены Mango, ElevenLabs и YandexGPT, руками меняется только коэффициент."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from tests.test_crm import DBCase

from crm import robot_prices as rp, robot_store

# Кусок прайса Yandex AI Studio: сначала синхронный режим (рубли с НДС), потом цены в долларах, потом асинхронный.
YANDEX_PAGE = """<html><head><title>Правила тарификации для Yandex AI Studio</title></head><body>
<h2>Синхронный режим</h2><table><tr><th>Модель</th><th>Цена за 1000 входящих токенов, вкл. НДС</th>
<th>кешированных</th><th>инструментов</th><th>Цена за 1000 исходящих токенов, вкл. НДС</th></tr>
<tr><td><p>YandexGPT Pro 5.1</p></td><td>0,8&nbsp;₽</td><td>0,8&nbsp;₽</td><td>0,2&nbsp;₽</td><td>0,8&nbsp;₽</td></tr>
<tr><td><p>YandexGPT Pro 5</p></td><td>1,2&nbsp;₽</td><td>1,2&nbsp;₽</td><td>1,2&nbsp;₽</td><td>1,2&nbsp;₽</td></tr>
<tr><td><p>YandexGPT Lite</p></td><td>0,2&nbsp;₽</td><td>0,2&nbsp;₽</td><td>0,2&nbsp;₽</td><td>0,4&nbsp;₽</td></tr></table>
<table><tr><td>YandexGPT Pro 5.1</td><td>0,006557376 $</td><td>0,006557376 $</td></tr></table>
<h2>Асинхронный режим</h2><table><tr><td>YandexGPT Pro 5.1</td><td>0,41 ₽</td><td>0,41 ₽</td></tr></table>
</body></html>"""

SUBSCRIPTION = {'tier': 'creator', 'character_limit': 100000, 'currency': 'usd', 'billing_period': 'monthly_period',
                'character_refresh_period': 'monthly_period', 'next_invoice': {'amount_due_cents': 2200}}
MODELS = [{'model_id': 'eleven_flash_v2_5', 'model_rates': {'character_cost_multiplier': 0.5, 'cost_discount_multiplier': 1.0}},
          {'model_id': 'eleven_v3', 'model_rates': {'character_cost_multiplier': 1.0}}]


class ParseTest(unittest.TestCase):
    def test_mango(self):
        raws = [{'context_type': 2, 'talk_duration': 120, 'context_cost_full': '3,0'},
                {'context_type': 2, 'talk_duration': 60, 'context_calls': [{'cost': 1}, {'cost': '0.5'}]},
                {'context_type': 2, 'talk_duration': 600}]  # без стоимости не считается вовсе
        value, note = rp.mango_rate(raws)
        self.assertEqual(value, 1.5)  # 4,5 ₽ за 3 минуты
        self.assertIn('за 3 мин', note)
        with self.assertRaisesRegex(rp.PriceError, 'не присылает стоимость'):
            rp.mango_rate([{'talk_duration': 60}])
        with self.assertRaisesRegex(rp.PriceError, 'нет исходящих разговоров'):
            rp.mango_rate([{'talk_duration': 0, 'cost': 0}])

    def test_cbr(self):
        xml = ('<ValCurs><Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal>'
               '<Name>Доллар США</Name><Value>81,5432</Value></Valute><Valute><CharCode>KZT</CharCode><Nominal>100</Nominal>'
               '<Name>Тенге</Name><Value>16,02</Value></Valute></ValCurs>')
        self.assertEqual(rp.cbr_rate(xml, 'usd'), 81.5432)
        self.assertAlmostEqual(rp.cbr_rate(xml, 'KZT'), 0.1602)
        with self.assertRaises(rp.PriceError):
            rp.cbr_rate(xml, 'eur')
        mirror = {'Valute': {'USD': {'CharCode': 'USD', 'Nominal': 1, 'Value': 81.5432}, 'KZT': {'Nominal': 100, 'Value': 16.02}}}
        self.assertEqual(rp.mirror_rate(mirror, 'usd'), 81.5432)
        self.assertAlmostEqual(rp.mirror_rate(mirror, 'kzt'), 0.1602)
        with self.assertRaises(rp.PriceError):
            rp.mirror_rate(mirror, 'eur')

    def test_eleven(self):
        value, note = rp.eleven_rate(SUBSCRIPTION, MODELS, 'eleven_flash_v2_5', 80)
        self.assertAlmostEqual(value, 8.8)  # 22 $ / 100 000 кредитов × 0,5 кредита за символ × 1000 × 80 ₽
        self.assertIn('creator', note)
        value, _ = rp.eleven_rate(SUBSCRIPTION, MODELS, 'eleven_v3', 80)
        self.assertAlmostEqual(value, 17.6)
        # годовой счёт, лимит обновляется раз в месяц: делим на 12 лимитов
        yearly = dict(SUBSCRIPTION, billing_period='annual_period', next_invoice={'amount_due_cents': 0, 'subtotal_cents': 26400})
        value, _ = rp.eleven_rate(yearly, MODELS, 'eleven_v3', 80)
        self.assertAlmostEqual(value, 17.6)
        with self.assertRaises(rp.PriceError):
            rp.eleven_rate(dict(SUBSCRIPTION, next_invoice=None), MODELS, 'eleven_v3', 80)

    def test_yandex(self):
        self.assertEqual(rp.yandex_name('gpt://b1g/yandexgpt-5.1/latest'), 'YandexGPT Pro 5.1')
        self.assertEqual(rp.yandex_name('yandexgpt/latest'), 'YandexGPT Pro 5')
        with self.assertRaises(rp.PriceError):
            rp.yandex_name('llama/latest')
        self.assertEqual(rp.yandex_price(YANDEX_PAGE, 'YandexGPT Pro 5.1')[0], 0.8)  # не асинхронные 0,41
        self.assertEqual(rp.yandex_price(YANDEX_PAGE, 'YandexGPT Pro 5')[0], 1.2)  # не строка Pro 5.1
        value, note = rp.yandex_price(YANDEX_PAGE, 'YandexGPT Lite')
        self.assertAlmostEqual(value, 0.3)  # вход и выход пополам
        self.assertIn('0,2 ₽ вход, 0,4 ₽ выход', note)
        with self.assertRaises(rp.PriceError):
            rp.yandex_price('<p>Цены скоро будут</p>', 'YandexGPT Pro 5.1')
        # таблица только внутри JSON в скрипте, с экранированными тегами и знаком рубля
        packed = ('<html><body><div id="root"></div><script>window.__DATA__={"html":"' +
                  json.dumps(YANDEX_PAGE.split('<body>')[1].replace('\n', ''))[1:-1].replace('<', '\\u003c').replace('>', '\\u003e')
                  + '"}</script></body></html>')
        self.assertNotIn('<td>', packed)
        self.assertEqual(rp.yandex_price(packed, 'YandexGPT Pro 5.1')[0], 0.8)


class RefreshTest(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.pool.execute('ALTER TABLE calls ADD COLUMN IF NOT EXISTS raw_summary JSONB')  # как на сервере

    async def test_mango_from_calls(self):
        at = datetime.now(timezone.utc) - timedelta(days=1)
        for raw in ({'context_type': 2, 'talk_duration': 120, 'context_cost_full': 4},
                    {'context_type': 1, 'talk_duration': 600, 'context_cost_full': 0}):  # входящие не в счёт
            await self.pool.execute("INSERT INTO calls(client_id,extension,call_started_at,raw_summary) VALUES(1,'101',$1,$2::jsonb)",
                                    at, json.dumps(raw))
        self.assertEqual((await rp.mango(self.pool))[0], 2.0)

    async def test_failed_source_keeps_last_value(self):
        ok = {'mango': AsyncMock(return_value=(1.5, 'mango')), 'eleven': AsyncMock(return_value=(8.8, 'eleven')),
              'yandex': AsyncMock(return_value=(0.8, 'yandex'))}
        with patch.multiple(rp, **ok):
            got = await rp.refresh(self.pool, http=object())
        self.assertEqual({k: v['value'] for k, v in got.items()}, {'mango_min': 1.5, 'eleven_1k_chars': 8.8, 'llm_1k_tokens': 0.8})
        await robot_store.set_option(self.pool, 'prices', {'multiplier': 3})
        self.assertEqual(await robot_store.prices(self.pool),
                         {'mango_min': 1.5, 'eleven_1k_chars': 8.8, 'llm_1k_tokens': 0.8, 'multiplier': 3.0})

        bad = dict(ok, eleven=AsyncMock(side_effect=rp.PriceError('ключу ElevenLabs не хватает права User → Read')),
                   yandex=AsyncMock(side_effect=OSError('timeout')))
        with patch.multiple(rp, **bad):
            got = await rp.refresh(self.pool, http=object())
        self.assertEqual((got['eleven_1k_chars']['value'], got['eleven_1k_chars']['note']), (8.8, 'eleven'))
        self.assertIn('User → Read', got['eleven_1k_chars']['error'])
        self.assertEqual((got['llm_1k_tokens']['value'], got['llm_1k_tokens']['error']), (0.8, 'источник не ответил (OSError)'))
        self.assertIsNone(got['mango_min']['error'])
        self.assertEqual((await robot_store.prices(self.pool))['eleven_1k_chars'], 8.8)

    async def test_rate_from_mirror_when_cbr_refuses(self):
        import httpx

        def answer(request):
            if request.url.host == 'www.cbr.ru':
                return httpx.Response(403)
            return httpx.Response(200, json={'Valute': {'USD': {'Nominal': 1, 'Value': 80.0}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            self.assertEqual(await rp.rub_per(http, 'usd'), 80.0)

    async def test_probe_reports_without_numbers(self):
        import httpx
        await self.pool.execute("INSERT INTO calls(client_id,extension,call_started_at,raw_summary) VALUES(1,'101',now(),$1::jsonb)",
                                json.dumps({'context_type': 2, 'talk_duration': 60, 'to_number': '79990001122',
                                            'context_calls': [{'call_end_reason': 1}]}))

        def answer(request):
            if 'elevenlabs' in request.url.host:
                return httpx.Response(401, json={'detail': {'status': 'missing_permissions', 'message': 'user_read'}})
            return httpx.Response(200, text=YANDEX_PAGE)
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'k', 'VOICE_MODEL': 'yandexgpt-5.1/latest'}), \
                patch('mango_client.call', AsyncMock(return_value={'result': 1000, 'balance': 512.3})) as mango_call:
            lines = await rp.probe(self.pool, httpx.AsyncClient(transport=httpx.MockTransport(answer)))
        text = '\n'.join(lines)
        self.assertIn('fields: context_calls, context_type, talk_duration, to_number; leg fields: call_end_reason', text)
        self.assertNotIn('79990001122', text)
        self.assertIn('subscription answered 401', text)
        self.assertIn('mango balance: {"result": 1000, "balance": 512.3}', text)
        self.assertEqual(mango_call.call_args.args[0], 'account/balance')
        self.assertIn('yandexgpt x', text)

    async def test_yandex_only_when_robot_uses_it(self):
        with patch.dict(os.environ, {'VOICE_LLM': 'cvc'}):
            with self.assertRaisesRegex(rp.PriceError, 'не через YandexGPT'):
                await rp.yandex(object())


if __name__ == '__main__':
    unittest.main()
