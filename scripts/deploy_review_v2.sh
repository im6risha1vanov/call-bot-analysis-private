#!/usr/bin/env bash
# Выкатка разбора по результату (CALL_REVIEW_MODE=v2) на сервер.
# Запуск под root:
#   cd /opt/callbot-astra && git fetch private-backup claude/project-thread-6ush05 \
#     && git show FETCH_HEAD:scripts/deploy_review_v2.sh > /root/deploy_review_v2.sh \
#     && bash /root/deploy_review_v2.sh
# Откат: bash /root/review-v2-rollback/rollback_review_v2.sh (сохраняет таблицы, возвращает код, .env и время сводки).
set -euo pipefail
cd /opt/callbot-astra
STATE=/root/review-v2-rollback
SERVICES="callbot-astra callbot-astra-worker callbot-astra-queue-runner callbot-astra-trainer"

if [ -n "$(git status --porcelain)" ]; then
  echo "СТОП: в /opt/callbot-astra есть незакоммиченные изменения"; git status --short; exit 1
fi
mkdir -p "$STATE"
git rev-parse HEAD > "$STATE/head"
git rev-parse --abbrev-ref HEAD > "$STATE/branch"
cp -p .env "$STATE/env"
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
psql "$DATABASE_URL" -AtF'|' -v ON_ERROR_STOP=1 -c "SELECT id, digest_time FROM clients ORDER BY id" > "$STATE/digest_time"
echo "Точка отката сохранена в $STATE ($(cat "$STATE/head"))"

git checkout -q -B review-v2 FETCH_HEAD
cp scripts/rollback_review_v2.sh "$STATE/"
echo "Код: $(git log -1 --oneline)"
.venv/bin/pip install -q -r requirements.txt
psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f migrations/008_call_reviews.sql -f migrations/009_callback_tasks.sql
echo "Миграции 008, 009 применены"

add_env() { grep -q "^$1=" .env || printf '%s=%s\n' "$1" "$2" >> .env; }
add_env CALL_REVIEW_MODE v2
add_env TRAIN_BOT_USERNAME averon_training_bot
add_env GOOGLE_SHEET_ID 1chzMdgtuHf2ipvaVH6zEuY_aHAEolr15NqB9NMAZrKU
add_env GOOGLE_SERVICE_ACCOUNT_FILE /opt/callbot-astra/google-key.json
psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -c "UPDATE clients SET digest_time='19:00'"
echo "Настройки добавлены, вечерняя сводка в 19:00"

systemctl restart $SERVICES
sleep 8
for s in $SERVICES; do echo "$s: $(systemctl is-active "$s")"; done
journalctl -u callbot-astra-worker -u callbot-astra-queue-runner --since '-1 min' --no-pager -p err | tail -20 || true
echo "Готово"
