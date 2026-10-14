# Голосовой оператор (робот-прозвонщик)

Робот звонит лиду, ведёт разговор по анкете оператора и записывает итог. Первый
этап без телефонии: проверяем разговор текстом и голосом из браузера. SIP Mango
подключается следующим шагом к тому же `CallSession`.

## Из чего состоит

| Файл | Что делает |
|---|---|
| `operators/kitchen.toml` | Анкета оператора, как в кабинете Соловья: имя, приветствие, план (до 10 шагов), правила, факты, поля для таблицы. Поведение робота меняют здесь. |
| `operator.py` | Читает и проверяет анкету, собирает из неё системный промпт. |
| `llm.py` | Модель: прокси ai.starimg.ru (Responses API, поток) или Claude напрямую. |
| `dialog.py` | Разговор с моделью потоком. Звонок заканчивается только инструментом `finish_call` или `transfer_to_specialist`, итог всегда структурный. |
| `call.py` | Один звонок: озвучка по фразам, перебивание, молчание, запись итога. Не зависит от транспорта. |
| `speech.py` | Deepgram (поток) и Yandex SpeechKit (PCM 16 кГц), нарезка ответа на фразы. |
| `results.py` | Итог в `VOICE_LOG` (jsonl) и, если задан `VOICE_SHEET_ID`, строкой на лист «Робот · Звонки». |
| `simulate.py` | Прогон текстом против симулированных клиентов с известным правильным итогом. |
| `autotest.py` | Виртуальный абонент говорит с телефонным кодом робота по AudioSocket (без Mango): распознавание, паузы, перебивания, модель, синтез, итог. Результат на лист «Робот · Автотест». На сервере запускается таймером после каждого коммита ветки (`voice_test.sh watch-install`), аргументы в `voice/autotest.args`, не больше 12 прогонов в сутки. |
| `web.py`, `static/index.html` | Страница теста: «Позвонить», говоришь в микрофон или пишешь текстом, в конце карточка итога. |

## Телефон (Mango)

`sip.py`: Asterisk на сервере зарегистрирован в Mango как сотрудник «Робот» (`scripts/voice_sip_setup.sh`,
нужны `MANGO_SIP_LOGIN`, `MANGO_SIP_DOMAIN`, `MANGO_SIP_PASSWORD` в `.env`). Звонок заказывается через ARI,
ответивший клиент приходит к роботу по AudioSocket (8 кГц), дальше тот же `CallSession`, что и в браузере.
Не взял трубку: итог `ndz` «Не взял трубку». `VOICE_CALLER_ID`, `VOICE_RING_TIMEOUT` (40 с), `VOICE_SIP_TRUNK` (`mango`).
Запуск: `scripts/voice_test.sh sip`, звонок: `scripts/voice_test.sh dial 7999…`.

## Итоги звонка

`interested` (подходит, специалист перезвонит или перевод), `callback`, `not_relevant`,
`ndz` (автоответчик, не тот человек), `do_not_call` (стоп-лист).

## Переменные

Берутся из `.env` бота: `CVC_API_KEY` (или `ANTHROPIC_API_KEY`), `DEEPGRAM_API_KEY`, `YANDEX_TTS_API_KEY`,
`GOOGLE_SERVICE_ACCOUNT_FILE`. Свои, все необязательные:

- `VOICE_LLM`: `cvc` (по умолчанию, прокси ai.starimg.ru, как у разборов), `openrouter` (ключ `OPENROUTER_API_KEY`, модель по умолчанию `anthropic/claude-haiku-5.5`) или `anthropic` (Claude напрямую).
- `VOICE_MODEL`: для cvc по умолчанию `CVC_MODEL` бота, для anthropic `claude-haiku-5-5`. `VOICE_CVC_EFFORT` (`low`), `VOICE_EFFORT` (`low`, для anthropic).
- `VOICE_SIM_MODEL` модель клиента в симуляторе (по умолчанию та же, что у робота).
- `OPENROUTER_API_KEY`: включает голоса ElevenLabs (в списке с префиксом `el:`), `VOICE_ELEVEN_MODEL` (`elevenlabs/eleven-flash-v2.5`), `VOICE_ELEVEN_PCM_RATE` (24000, если провайдер не пишет частоту).
- `ELEVENLABS_API_KEY`: ElevenLabs напрямую, в списке все голоса аккаунта (и клоны) с префиксом `11:`; в анкете можно `voice = "11:<voice_id>"`. `VOICE_ELEVEN_DIRECT_MODEL` (`eleven_flash_v2_5`).
- `VOICE_TTS_API` (`v3`, интонации; при отказе доступа сам уходит на v1), `VOICE_TTS_RATE` (48000), `VOICE_ENDPOINTING_MS` (300, сколько тишины считать концом фразы), `VOICE_DG_MODEL`.
- `VOICE_SHEET_ID` таблица для листа «Робот · Звонки», `VOICE_LOG` путь к jsonl.
- `VOICE_WEB_HOST` / `VOICE_WEB_PORT` (127.0.0.1:8090).

## Запуск на сервере

`scripts/voice_test.sh start | sim | stop | status`, подробности в шапке скрипта.
Код ставится отдельно в `/opt/callbot-voice`, рабочий бот не перезапускается.

## Тесты

`python -m pytest tests/test_voice.py` (модель, синтез и Google подменены, ключи не нужны).
