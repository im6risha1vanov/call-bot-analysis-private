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
#           dial НОМЕР (робот звонит на номер), stop, status.
# Страница открывается через туннель: ssh -L 8090:127.0.0.1:8090 root@SERVER, потом http://localhost:8090
set -euo pipefail
ACTION=${1:-start}
BOT=/opt/callbot-astra
DIR=/opt/callbot-voice
DATA=/opt/callbot-voice-data
REMOTE=${VOICE_REMOTE:-private-backup}
BRANCH=claude/voice-operator
UNIT=callbot-voice-test
SIP_UNIT=callbot-voice-sip
PY=$BOT/.venv/bin/python

update() {
  cd "$BOT"
  git fetch -q "$REMOTE" "$BRANCH"
  SHA=$(git rev-parse FETCH_HEAD)
  if [ -d "$DIR" ]; then git -C "$DIR" checkout -q --detach "$SHA"; else git worktree add -q --detach "$DIR" "$SHA"; fi
  mkdir -p "$DATA"
  chown -R callbot-astra: "$DATA"
  chmod -R a+rX "$DIR"
  "$PY" -c "import aiohttp, anthropic, httpx" || { echo "в .venv нет aiohttp/anthropic/httpx"; exit 1; }
  echo "код: $(git -C "$DIR" log -1 --format='%h %s')"
}

run() {  # одноразовый запуск с ключами бота от пользователя бота
  systemd-run --quiet --wait --pipe --collect --uid=callbot-astra --working-directory="$DIR" \
    -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" -E VOICE_TTS_CACHE="$DATA/tts-cache" "$PY" "$@"
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
  sim)
    update
    run -m voice.simulate --out "$DATA/sim.md" "${@:2}" || true
    echo "разговоры: $DATA/sim.md"
    ;;
  sip)
    update
    systemctl stop "$SIP_UNIT" 2>/dev/null || true
    systemctl reset-failed "$SIP_UNIT" 2>/dev/null || true
    systemd-run --quiet --unit="$SIP_UNIT" --uid=callbot-astra --working-directory="$DIR" \
      -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" -E VOICE_TTS_CACHE="$DATA/tts-cache" \
      -p Restart=on-failure "$PY" -m voice.sip serve
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
    journalctl -u "$SIP_UNIT" --since "-70s" --no-pager | grep -v aiohttp.access | tail -40
    ;;
  stop)
    systemctl stop "$UNIT" "$SIP_UNIT" 2>/dev/null || true
    ;;
  status)
    for u in "$UNIT" "$SIP_UNIT"; do systemctl status "$u" --no-pager 2>/dev/null | head -3 || true; done
    journalctl -u "$UNIT" -u "$SIP_UNIT" -n 40 --no-pager
    ;;
  *)
    echo "действие: start | sim | sip | dial НОМЕР | stop | status"; exit 2 ;;
esac
