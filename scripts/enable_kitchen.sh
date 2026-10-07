#!/usr/bin/env bash
# Включает автозаполнение прозвона по скрипту кухонь (review/kitchen.py).
# Запуск на сервере из /opt/callbot-astra: bash scripts/enable_kitchen.sh <добавочный> [Имя]
# Откат: cp "$STATE/env.before_kitchen" .env и перезапуск worker и queue-runner (таблица lead_attempts не мешает).
set -euo pipefail
EXT=${1:?добавочный оператора}
NAME=${2:-Ольга}
SHEET=1mH4MXECrtCStfCpf54DK50_rCOwVVDUir8c_Tz3OVX0
STATE=/root/review-v2-rollback
cd /opt/callbot-astra
mkdir -p "$STATE"
cp .env "$STATE/env.before_kitchen"
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f migrations/010_lead_attempts.sql

set_env() {  # заменяет или дописывает ключ
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_env KITCHEN_EXTENSIONS "$EXT"
set_env KITCHEN_MANAGER "$NAME"
set_env KITCHEN_SHEET_ID "$SHEET"
grep -q '^KITCHEN_SINCE=' .env || echo "KITCHEN_SINCE=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" >> .env
grep '^KITCHEN_\|^GOOGLE_SERVICE_ACCOUNT_FILE' .env

systemctl restart callbot-astra-worker callbot-astra-queue-runner
sleep 5
systemctl is-active callbot-astra-worker callbot-astra-queue-runner
