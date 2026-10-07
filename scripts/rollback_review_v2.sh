#!/usr/bin/env bash
# Откат выкатки scripts/deploy_review_v2.sh: прежний код, прежний .env и время
# сводки. Таблицы call_reviews и callback_tasks остаются, прежний код их не читает.
# Запуск под root: bash /root/review-v2-rollback/rollback_review_v2.sh
# (копию кладёт deploy: сам файл исчезает из рабочей папки при возврате кода)
set -euo pipefail
cd /opt/callbot-astra
STATE=/root/review-v2-rollback
SERVICES="callbot-astra callbot-astra-worker callbot-astra-queue-runner callbot-astra-trainer"
[ -f "$STATE/head" ] || { echo "Нет точки отката в $STATE"; exit 1; }

git checkout -q "$(cat "$STATE/branch")"
[ "$(git rev-parse HEAD)" = "$(cat "$STATE/head")" ] || { echo "СТОП: ветка $(cat "$STATE/branch") сдвинулась"; exit 1; }
cp -p "$STATE/env" .env
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
while IFS='|' read -r id t; do
  psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -c "UPDATE clients SET digest_time='$t' WHERE id=$id"
done < "$STATE/digest_time"
systemctl restart $SERVICES
sleep 8
for s in $SERVICES; do echo "$s: $(systemctl is-active "$s")"; done
echo "Откат готов: $(git log -1 --oneline)"
