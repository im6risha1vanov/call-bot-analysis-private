#!/usr/bin/env bash
# Копии всех разборов менеджеров (кроме прозвона KITCHEN_EXTENSIONS) руководителям.
# Запуск на сервере из /opt/callbot-astra:
#   bash scripts/review_copies.sh                  — показать сотрудников бота и их Telegram ID
#   bash scripts/review_copies.sh ID1 ID2 head:x   — включить копии: Telegram ID или добавочный сотрудника
#                                                   (его ID подставится сам после /start)
#   bash scripts/review_copies.sh off              — выключить копии
# Человек должен хотя бы раз нажать /start у бота, иначе Telegram не даст ему написать.
set -euo pipefail
cd /opt/callbot-astra
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
if ! psql "$DATABASE_URL" -Atc "SELECT 1 FROM information_schema.columns WHERE table_name='call_reviews' AND column_name='copy_sent_at'" | grep -q 1; then
  psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f migrations/011_review_copies.sql
fi
if [ $# -eq 0 ]; then
  psql "$DATABASE_URL" -c "SELECT role, extension, full_name, telegram_user_id FROM employees ORDER BY role, full_name"
  grep '^REVIEW_COPY_TO=' .env || echo 'REVIEW_COPY_TO не задан'
  exit 0
fi
cp .env "/root/review-v2-rollback/env.before_review_copies.$(date +%s)" 2>/dev/null || true
if [ "$1" = off ]; then
  sed -i '/^REVIEW_COPY_TO=/d' .env
else
  IDS=$(echo "$*" | tr ' ' ',')
  if grep -q '^REVIEW_COPY_TO=' .env; then sed -i "s|^REVIEW_COPY_TO=.*|REVIEW_COPY_TO=$IDS|" .env; else echo "REVIEW_COPY_TO=$IDS" >> .env; fi
fi
grep '^REVIEW_COPY_TO=' .env || echo 'копии выключены'
systemctl restart callbot-astra-worker callbot-astra-queue-runner
sleep 5
systemctl is-active callbot-astra-worker callbot-astra-queue-runner
