#!/usr/bin/env bash
# Тест голосового оператора на сервере. Рабочего бота не трогает: код ветки
# claude/voice-operator лежит отдельно в /opt/callbot-voice, ключи берутся из
# /opt/callbot-astra/.env, Python из /opt/callbot-astra/.venv.
#
# Запуск (из PowerShell одной строкой, только латиница):
#   ssh root@SERVER "cd /opt/callbot-astra && git fetch -q private-backup claude/voice-operator && git show FETCH_HEAD:scripts/voice_test.sh | bash -s start"
# Действия: start (обновить код и поднять страницу теста на 127.0.0.1:8090),
#           sim (прогнать робота текстом против симулированных клиентов),
#           sip (обновить код и поднять телефонного робота, нужен scripts/voice_sip_setup.sh),
#           dial НОМЕР (робот звонит на номер), stop, status,
#           autotest [АРГУМЕНТЫ] (виртуальный абонент говорит с роботом, итог на лист «Робот · Автотест»),
#           watch-install / watch-remove (таймер: автотест после каждого нового коммита ветки),
#           tick (то, что запускает таймер).
# Страница открывается через туннель: ssh -L 8090:127.0.0.1:8090 root@SERVER, потом http://localhost:8090
set -euo pipefail
ACTION=${1:-start}
BOT=/opt/callbot-astra
DIR=/opt/callbot-voice
DATA=/opt/callbot-voice-data
REMOTE=${VOICE_REMOTE:-private-backup}
BRANCH=claude/voice-operator
UNIT=callbot-voice-test
WATCH=callbot-voice-autotest
MAX_PER_DAY=${VOICE_AUTOTEST_MAX_PER_DAY:-12}
SIP_UNIT=callbot-voice-sip
PY=$BOT/.venv/bin/python
PYLIB=$DATA/pylib  # библиотеки только для робота (распознавание Яндекса), .venv бота не трогаем
ASR_ENV=(-E PYTHONPATH="$PYLIB" -E VOICE_ASR=${VOICE_ASR:-yandex})

update() {
  cd "$BOT"
  git fetch -q "$REMOTE" "$BRANCH"
  SHA=$(git rev-parse FETCH_HEAD)
  if [ -d "$DIR" ]; then git -C "$DIR" checkout -q --detach "$SHA"; else git worktree add -q --detach "$DIR" "$SHA"; fi
  mkdir -p "$DATA"
  if ! PYTHONPATH="$PYLIB" "$PY" -c "import grpc; from yandex.cloud.ai.stt.v3 import stt_pb2" 2>/dev/null; then
    echo "ставлю библиотеки распознавания Яндекса в $PYLIB"
    "$PY" -m pip install -q --upgrade --target "$PYLIB" grpcio protobuf googleapis-common-protos \
      && "$PY" -m pip install -q --upgrade --target "$PYLIB" --no-deps yandexcloud \
      || echo "библиотеки не встали: робот распознаёт через Deepgram"
  fi
  chown -R callbot-astra: "$DATA"
  chmod -R a+rX "$DIR"
  "$PY" -c "import aiohttp, anthropic, httpx" || { echo "в .venv нет aiohttp/anthropic/httpx"; exit 1; }
  echo "код: $(git -C "$DIR" log -1 --format='%h %s')"
}

run() {  # одноразовый запуск с ключами бота от пользователя бота; доп. переменные: RUN_ENV=(-E K=V ...)
  systemd-run --quiet --wait --pipe --collect --uid=callbot-astra --working-directory="$DIR" \
    -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" -E VOICE_TTS_CACHE="$DATA/tts-cache" \
    "${RUN_ENV[@]}" "$PY" "$@" </dev/null
}
RUN_ENV=()

start_sip() {  # телефонный робот (AudioSocket 9092, звонки через 8092)
  systemctl stop "$SIP_UNIT" 2>/dev/null || true
  systemctl reset-failed "$SIP_UNIT" 2>/dev/null || true
  systemd-run --quiet --unit="$SIP_UNIT" --uid=callbot-astra --working-directory="$DIR" \
    -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" -E VOICE_TTS_CACHE="$DATA/tts-cache" \
    -E VOICE_TTS_RATE=8000 -E VOICE_ELEVEN_PCM_RATE=8000 -E VOICE_ASR_DEBUG=1 "${ASR_ENV[@]}" \
    -p Restart=on-failure "$PY" -m voice.sip serve
}

case "$ACTION" in
  start)
    update
    systemctl stop "$UNIT" 2>/dev/null || true
    systemctl reset-failed "$UNIT" 2>/dev/null || true
    systemd-run --quiet --unit="$UNIT" --uid=callbot-astra --working-directory="$DIR" \
      -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" -E VOICE_TTS_CACHE="$DATA/tts-cache" -p Restart=on-failure \
      "$PY" -m voice.web
    sleep 3
    systemctl is-active "$UNIT"
    curl -s http://127.0.0.1:8090/operators; echo
    ;;
  bench)  # скорость моделей: bench МОДЕЛЬ...
    update
    run -m voice.bench "${@:2}" || true
    ;;
  sim)
    update
    run -m voice.simulate --out "$DATA/sim.md" "${@:2}" || true
    echo "разговоры: $DATA/sim.md"
    ;;
  sip)
    update
    start_sip
    sleep 3
    systemctl is-active "$SIP_UNIT"
    if systemctl is-active -q callbot-voice-relay; then  # Asterisk на российском VPS (voice_relay_setup.sh)
      VPS=$(grep -o 'root@[0-9.]*' /etc/systemd/system/callbot-voice-relay.service | cut -d@ -f2)
      ssh -i /root/.ssh/voice_relay -o BatchMode=yes "root@$VPS" 'asterisk -rx "pjsip show registrations"' | grep -i mango
    else
      asterisk -rx "pjsip show registrations" 2>/dev/null | grep -i mango || echo "asterisk не настроен: bash $DIR/scripts/voice_sip_setup.sh"
    fi
    ;;
  dial)
    PHONE=${2:?номер}
    curl -s -X POST http://127.0.0.1:8092/dial -H 'Content-Type: application/json' -d "{\"phone\": \"$PHONE\"}"; echo
    sleep 60
    journalctl -u "$SIP_UNIT" --since "-70s" --no-pager | grep -v -e aiohttp.access -e "httpx INFO" | tail -80
    ;;
  autotest)
    update
    if systemctl is-active -q "$SIP_UNIT"; then start_sip; fi  # телефонный робот на новом коде и настройках
    RUN_ENV=(-E VOICE_TTS_RATE=8000 -E VOICE_ELEVEN_PCM_RATE=8000 -E VOICE_AUTOTEST_SHA="$SHA" "${ASR_ENV[@]}")
    run -m voice.autotest --sheet --out "$DATA/autotest.md" "${@:2}" || true
    echo "отчёт: $DATA/autotest.md"
    ;;
  tick)  # таймер: новый коммит с правками робота -> автотест (не чаще MAX_PER_DAY в сутки)
    mkdir -p "$DATA"
    exec 9>"$DATA/autotest.lock"
    flock -n 9 || exit 0
    cd "$BOT"
    git fetch -q "$REMOTE" "$BRANCH"
    SHA=$(git rev-parse FETCH_HEAD)
    LAST=$(cat "$DATA/autotest.sha" 2>/dev/null || true)
    [ "$SHA" = "$LAST" ] && exit 0
    echo "$SHA" > "$DATA/autotest.sha"
    CHANGED=$( { [ -n "$LAST" ] && git diff --name-only "$LAST" "$SHA" 2>/dev/null; } || echo voice/)
    if ! grep -qE '^(voice/|scripts/voice_|tests/test_voice)' <<<"$CHANGED"; then
      exit 0  # правки не про робота
    fi
    TODAY=$(date +%F)
    RUNS=$(grep -c "^$TODAY" "$DATA/autotest.runs" 2>/dev/null || true)
    if [ "${RUNS:-0}" -ge "$MAX_PER_DAY" ]; then
      echo "лимит $MAX_PER_DAY автотестов в сутки, $SHA пропущен"; exit 0
    fi
    echo "$TODAY $(date +%T) $SHA" >> "$DATA/autotest.runs"
    ARGS=$(git show "$SHA:voice/autotest.args" 2>/dev/null | grep -v '^#' | tr -cd 'a-z0-9_,= \n-' | head -1 || true)
    # shellcheck disable=SC2086
    git show "$SHA:scripts/voice_test.sh" | bash -s autotest $ARGS
    ;;
  watch-install)
    mkdir -p "$DATA"
    cat > /etc/systemd/system/$WATCH.service <<UNIT
[Unit]
Description=Voice robot autotest on new commits of $BRANCH
[Service]
Type=oneshot
TimeoutStartSec=1800
Environment=HOME=/root
ExecStart=/bin/bash -c 'cd $BOT && git fetch -q $REMOTE $BRANCH && git show FETCH_HEAD:scripts/voice_test.sh | bash -s tick'
UNIT
    cat > /etc/systemd/system/$WATCH.timer <<UNIT
[Unit]
Description=Check $BRANCH for new commits every 3 minutes
[Timer]
OnBootSec=2min
OnUnitInactiveSec=3min
[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now "$WATCH.timer"
    systemctl list-timers "$WATCH.timer" --no-pager | head -2
    ;;
  watch-remove)
    systemctl disable --now "$WATCH.timer" 2>/dev/null || true
    rm -f /etc/systemd/system/$WATCH.service /etc/systemd/system/$WATCH.timer
    systemctl daemon-reload
    echo "автотест по коммитам выключен"
    ;;
  stop)
    systemctl stop "$UNIT" "$SIP_UNIT" 2>/dev/null || true
    ;;
  status)
    for u in "$UNIT" "$SIP_UNIT"; do systemctl status "$u" --no-pager 2>/dev/null | head -3 || true; done
    journalctl -u "$UNIT" -u "$SIP_UNIT" -n 40 --no-pager
    ;;
  *)
    echo "действие: start | sim | bench МОДЕЛИ | sip | dial НОМЕР | autotest | watch-install | watch-remove | stop | status"; exit 2 ;;
esac
