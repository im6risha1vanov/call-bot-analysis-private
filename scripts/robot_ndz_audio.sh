#!/usr/bin/env bash
# Записи звонков робота для разбора автоответчиков: архив /root/robot-calls.tar.gz без номеров телефонов.
# В имени файла: итог, дата, длительность, токены. Берём до N звонков ndz и до M живых для сравнения.
# Запуск из /opt/callbot-astra: bash scripts/robot_ndz_audio.sh [дней] [N ndz] [M живых]
set -euo pipefail
[ -f .env ] || cd "$(dirname "$0")/.."  # через git show ... | bash запускаем из /opt/callbot-astra
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
DATA=$(grep -m1 '^VOICE_DATA_DIR=' .env | cut -d= -f2- || true)
REC=${DATA:-/opt/callbot-voice-data}/recordings
DAYS=${1:-10}; NDZ=${2:-40}; LIVE=${3:-10}
OUT=$(mktemp -d)
pick() {  # $1 условие на итог, $2 сколько
  psql "$DATABASE_URL" -At -F ' ' -c "
    SELECT recording, outcome || '_' || to_char(at AT TIME ZONE 'Europe/Moscow', 'MMDD-HH24MI') || '_' || duration_s || 's_' || llm_tokens || 't'
    FROM robot_calls WHERE recording IS NOT NULL AND $1 AND at > now() - interval '$DAYS days'
    ORDER BY at DESC LIMIT $2"
}
n=0
while read -r file name; do
  [ -n "$file" ] && [ -f "$REC/$file" ] || continue
  cp "$REC/$file" "$OUT/$name.wav"; n=$((n + 1))
done < <(pick "outcome = 'ndz'" "$NDZ"; pick "outcome <> 'ndz'" "$LIVE")
tar -czf /root/robot-calls.tar.gz -C "$OUT" .
rm -rf "$OUT"
echo "записей: $n, архив: /root/robot-calls.tar.gz $(du -h /root/robot-calls.tar.gz | cut -f1)"
