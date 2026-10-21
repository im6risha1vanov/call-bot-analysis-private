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
def sku(name, price, unit='1K tokens', when='2025-01-01T00:00:00Z', desc=''):
    return {'name': name, 'description': desc, 'pricingUnit': unit,
            'pricingVersions': [{'effectiveTime': when, 'pricingExpressions': [{'rates': [{'startPricingQuantity': '0', 'unitPrice': str(price)}]}]}]}


SKUS = [sku('YandexGPT Pro 5.1. Генерация', 0.8), sku('YandexGPT Pro 5.1. Асинхронный режим', 0.41),
        sku('YandexGPT Pro 5. Генерация', 1.2), sku('YandexGPT Lite, вход', 0.0002, unit='token'), sku('YandexGPT Lite, выход', 0.4),
        sku('Compute vCPU', 1.1, unit='core*hour'),
        {**sku('YandexGPT Pro 5.1. Генерация', 0.8), 'pricingVersions': [  # новая цена с будущего месяца ещё не действует
            {'effectiveTime': '2025-01-01T00:00:00Z', 'pricingExpressions': [{'rates': [{'startPricingQuantity': '0', 'unitPrice': '0.8'}]}]},
            {'effectiveTime': '2099-01-01T00:00:00Z', 'pricingExpressions': [{'rates': [{'startPricingQuantity': '0', 'unitPrice': '9'}]}]}]}]

MODELS = [{'model_id': 'eleven_flash_v2_5', 'model_rates': {'character_cost_multiplier': 0.5, 'cost_discount_multiplier': 1.0}},
          {'model_id': 'eleven_v3', 'model_rates': {'character_cost_multiplier': 1.0}}]


class ParseTest(unittest.TestCase):
    def test_mango(self):
        utc = timezone.utc
        t = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=utc)  # noqa: E731
        hist = lambda *pts: [{'at': at.isoformat(), 'balance': b} for at, b in pts]  # noqa: E731
        # 20.10: за час списано 30 ₽ при 20 минутах разговоров = 1,5 ₽/мин
        # 21.10: пополнение +1000 (час не считается), потом 40 ₽ на 20 минут = 2 ₽/мин
        # 22.10: абонентская плата 900 ₽ за час с 10 минутами = 90 ₽/мин, медиана её отсекает
        # 23.10: 5 минут разговоров — день слишком шумный и не считается
        history = hist((t(20, 9), 5000), (t(20, 10), 4970), (t(21, 9), 4970), (t(21, 10), 5960), (t(21, 11), 5920),
                       (t(22, 6), 5920), (t(22, 7), 5020), (t(23, 9), 5020), (t(23, 10), 5000))
        calls = [(t(20, 9, 10), 1200), (t(21, 9, 30), 600), (t(21, 10, 30), 1200), (t(22, 6, 30), 600), (t(23, 9, 5), 300)]
        value, note = rp.mango_rate(history, calls)
        self.assertEqual(value, 2.0)  # середина из 1,5 / 2 / 90
        self.assertIn('по 3 дн.', note)
        # разрыв в замерах дольше двух часов не считается
        with self.assertRaises(rp.NotYet):
            rp.mango_rate(hist((t(20, 9), 5000), (t(20, 12), 4970)), [(t(20, 10), 1200)])
        with self.assertRaises(rp.NotYet):
            rp.mango_rate([], [])

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

    def test_yandex_billing(self):
        self.assertEqual(rp.yandex_sku_rate(SKUS, 'YandexGPT Pro 5.1')[0], 0.8)  # не асинхронные 0,41 и не будущие 9
        self.assertEqual(rp.yandex_sku_rate(SKUS, 'YandexGPT Pro 5')[0], 1.2)
        value, note = rp.yandex_sku_rate(SKUS, 'YandexGPT Lite')
        self.assertAlmostEqual(value, 0.3)  # 0,0002 за токен = 0,2 за 1000; выход 0,4
        self.assertIn('0,2 ₽ – 0,4 ₽', note)
        with self.assertRaises(rp.PriceError):
            rp.yandex_sku_rate(SKUS[-2:-1], 'YandexGPT Pro 5.1')

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

    async def test_mango_from_balance(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for raw, ago in (({'context_type': 2, 'talk_duration': 1200}, 50), ({'context_type': 1, 'talk_duration': 600}, 40)):  # входящие не в счёт
            await self.pool.execute("INSERT INTO calls(client_id,extension,call_started_at,raw_summary) VALUES(1,'101',$1,$2::jsonb)",
                                    now - timedelta(minutes=ago), json.dumps(raw))
        await robot_store.set_option(self.pool, 'mango_balance', [{'at': (now - timedelta(hours=1)).isoformat(), 'balance': 100.0},
                                                                  {'at': (now - timedelta(days=40)).isoformat(), 'balance': 900.0}])
        with patch('mango_client.call', AsyncMock(return_value={'result': 1000, 'balance': '70.00'})) as call:
            await rp.snapshot(self.pool)
        self.assertEqual(call.call_args.args[0], 'account/balance')
        history = await robot_store.get_option(self.pool, 'mango_balance', [])
        self.assertEqual([h['balance'] for h in history], [100.0, 70.0])  # старше 31 дня выброшено
        self.assertEqual((await rp.mango(self.pool))[0], 1.5)  # 30 ₽ за 20 минут

        await robot_store.set_option(self.pool, 'mango_balance', [])
        with patch.multiple(rp, eleven=AsyncMock(return_value=(8.8, 'e')), yandex=AsyncMock(return_value=(0.8, 'y'))):
            got = await rp.refresh(self.pool, http=object())
        self.assertEqual((got['mango_min']['value'], got['mango_min']['error']), (None, None))  # данных мало — не ошибка
        self.assertIn('первая цена появится', got['mango_min']['note'])

    async def test_failed_source_keeps_last_value(self):
        ok = {'mango': AsyncMock(return_value=(1.5, 'mango')), 'eleven': AsyncMock(return_value=(8.8, 'eleven')),
              'yandex': AsyncMock(return_value=(0.8, 'yandex'))}
        with patch.multiple(rp, **ok):
            got = await rp.refresh(self.pool, http=object())
        self.assertEqual({k: v['value'] for k, v in got.items()}, {'mango_min': 1.5, 'eleven_1k_chars': 8.8, 'llm_1k_tokens': 0.8})
        await robot_store.set_option(self.pool, 'prices', {'client_min': {'Кухни': 12}})
        self.assertEqual(await robot_store.prices(self.pool),
                         {'mango_min': 1.5, 'eleven_1k_chars': 8.8, 'llm_1k_tokens': 0.8, 'tts_1k_chars': 8.8, 'client_min': {'Кухни': 12.0}})

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
            if 'billing' in request.url.host:
                return httpx.Response(200, json={'skus': SKUS})
            return httpx.Response(200, text=YANDEX_PAGE)
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'k', 'VOICE_MODEL': 'yandexgpt-5.1/latest', 'YANDEX_TTS_API_KEY': 'y',
                                     'YANDEX_SA_KEY_FILE': '/nonexistent.json'}), \
                patch('mango_client.call', AsyncMock(return_value={'result': 1000, 'balance': 512.3})):
            lines = await rp.probe(self.pool, httpx.AsyncClient(transport=httpx.MockTransport(answer)))
        text = '\n'.join(lines)
        self.assertIn('fields: context_calls, context_type, talk_duration, to_number; leg fields: call_end_reason', text)
        self.assertNotIn('79990001122', text)
        self.assertIn('subscription answered 401', text)
        self.assertIn('mango balance: 512,30 ₽', text)
        self.assertIn('yandexgpt x', text)
        self.assertIn('yandex billing: 6 gpt skus', text)
        self.assertIn('YandexGPT Pro 5.1. Генерация | 1K tokens | 0.8 |', text)

    async def test_yandex_billing_then_page(self):
        import httpx
        env = {'VOICE_LLM': 'yandex', 'VOICE_MODEL': 'yandexgpt-5.1/latest', 'YANDEX_GPT_API_KEY': 'y', 'YANDEX_SA_KEY_FILE': '/nonexistent.json'}
        seen = []

        def billing_ok(request):
            seen.append(request.headers.get('authorization'))
            return httpx.Response(200, json={'skus': SKUS})
        with patch.dict(os.environ, env):
            async with httpx.AsyncClient(transport=httpx.MockTransport(billing_ok)) as http:
                self.assertEqual((await rp.yandex(http))[0], 0.8)
        self.assertEqual(seen, ['Api-Key y'])

        def captcha(request):
            if 'billing' in request.url.host:
                return httpx.Response(403, json={'message': 'Permission denied'})
            return httpx.Response(200, text='<title>Верификация</title>')
        with patch.dict(os.environ, env):
            async with httpx.AsyncClient(transport=httpx.MockTransport(captcha)) as http:
                with self.assertRaisesRegex(rp.PriceError, r'не пускает ключ робота \(нужен ключ сервисного аккаунта\), код 403; страница прайса'):
                    await rp.yandex(http)

            def page_ok(request):
                return httpx.Response(401) if 'billing' in request.url.host else httpx.Response(200, text=YANDEX_PAGE)
            async with httpx.AsyncClient(transport=httpx.MockTransport(page_ok)) as http:
                self.assertEqual((await rp.yandex(http))[0], 0.8)

    async def test_billing_with_service_account_key(self):
        import base64
        import tempfile
        import httpx
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        key = {'id': 'aje1', 'service_account_id': 'aje2', 'private_key': 'PLEASE DO NOT REMOVE THIS LINE! Yandex.Cloud SA Key ID <aje1>\n' + pem}
        unb64 = lambda part: base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))  # noqa: E731
        seen = []

        def answer(request):
            if 'iam' in request.url.host:
                head, body, sig = json.loads(request.content)['jwt'].split('.')
                private.public_key().verify(unb64(sig), f'{head}.{body}'.encode(),
                                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
                claims = json.loads(unb64(body))
                seen.append((json.loads(unb64(head))['kid'], claims['iss'], claims['aud']))
                return httpx.Response(200, json={'iamToken': 't1.iam'})
            seen.append(request.headers['authorization'])
            return httpx.Response(200, json={'skus': SKUS})
        with tempfile.NamedTemporaryFile('w', suffix='.json') as f:
            json.dump(key, f)
            f.flush()
            with patch.dict(os.environ, {'YANDEX_SA_KEY_FILE': f.name, 'YANDEX_GPT_API_KEY': 'y'}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
                    self.assertEqual(len(await rp.yandex_skus(http)), len(SKUS))
        self.assertEqual(seen, [('aje1', 'aje2', rp.IAM_TOKENS), 'Bearer t1.iam'])

    async def test_yandex_only_when_robot_uses_it(self):
        with patch.dict(os.environ, {'VOICE_LLM': 'cvc'}):
            with self.assertRaisesRegex(rp.PriceError, 'не через YandexGPT'):
                await rp.yandex(object())


if __name__ == '__main__':
    unittest.main()

