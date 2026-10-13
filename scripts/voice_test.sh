#!/usr/bin/env bash
# Тест голосового оператора на сервере. Рабочего бота не трогает: код ветки
# claude/voice-operator лежит отдельно в /opt/callbot-voice, ключи берутся из
# /opt/callbot-astra/.env, Python из /opt/callbot-astra/.venv.
#
# Запуск (из PowerShell одной строкой, только латиница):
#   ssh root@SERVER "cd /opt/callbot-astra && git fetch -q private-backup claude/voice-operator && git show FETCH_HEAD:scripts/voice_test.sh | bash -s start"
# Действия: start (обновить код и поднять страницу теста на 127.0.0.1:8090),
#           sim (прогнать робота текстом против симулированных клиентов),
#           stop, status.
# Страница открывается через туннель: ssh -L 8090:127.0.0.1:8090 root@SERVER, потом http://localhost:8090
set -euo pipefail
ACTION=${1:-start}
BOT=/opt/callbot-astra
DIR=/opt/callbot-voice
DATA=/opt/callbot-voice-data
REMOTE=${VOICE_REMOTE:-private-backup}
BRANCH=claude/voice-operator
UNIT=callbot-voice-test
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
    -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" "$PY" "$@"
}

case "$ACTION" in
  start)
    update
    systemctl stop "$UNIT" 2>/dev/null || true
    systemctl reset-failed "$UNIT" 2>/dev/null || true
    systemd-run --quiet --unit="$UNIT" --uid=callbot-astra --working-directory="$DIR" \
      -p EnvironmentFile="$BOT/.env" -E VOICE_LOG="$DATA/calls.jsonl" -p Restart=on-failure \
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
  stop)
    systemctl stop "$UNIT"
    ;;
  status)
    systemctl status "$UNIT" --no-pager | head -5 || true
    journalctl -u "$UNIT" -n 30 --no-pager
    ;;
  *)
    echo "действие: start | sim | stop | status"; exit 2 ;;
esac
