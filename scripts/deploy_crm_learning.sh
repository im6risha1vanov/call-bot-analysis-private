#!/usr/bin/env bash
# Run from a generated private release package, not from the repository directly.
# No migrations, dependency installation, settings edits or bot/Caddy restarts.
set -euo pipefail
umask 077
APP=/opt/callbot-astra
UNIT=callbot-crm.service
PYTHON="$APP/.venv/bin/python"
DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-apply}
case "$MODE" in apply|check|rollback) ;; *) echo 'Use apply, check or rollback.' >&2; exit 2;; esac
[[ "$EUID" -eq 0 ]] || { echo 'STOP: run under root using the normal server SSH session.' >&2; exit 1; }
case "$(hostname)" in 9015421-nt422325|9015421-nt422325.*) ;; *) echo 'STOP: unexpected server.' >&2; exit 1;; esac
cd "$APP"
META=$("$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["base"], d["target"], d["source_ref"])' "$DIR/release.json")
read -r BASE TARGET SOURCE_REF <<<"$META"
[[ "$BASE" =~ ^[a-f0-9]{40}$ && "$TARGET" =~ ^[a-f0-9]{40}$ && "$SOURCE_REF" =~ ^refs/heads/[a-zA-Z0-9_./-]+$ ]] || { echo 'STOP: invalid release metadata.' >&2; exit 1; }
(cd "$DIR" && sha256sum --check --status SHA256SUMS)
[[ $(git rev-parse --show-toplevel) == "$APP" ]] || { echo 'STOP: wrong repository.' >&2; exit 1; }
# This check must happen before ANY application or Git mutation.
systemctl is-active --quiet "$UNIT" || { echo 'STOP: CRM is inactive or systemd is unavailable. No code changed.' >&2; exit 1; }
[[ $(systemctl show "$UNIT" -p WorkingDirectory --value) == "$APP" ]] || { echo 'STOP: unexpected CRM working directory.' >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo 'STOP: tracked local edits. No files overwritten.' >&2; exit 1; }
if [[ "$MODE" == check || "$MODE" == apply ]]; then
  [[ $(git rev-parse HEAD) == "$BASE" ]] || { echo 'STOP: production commit changed; rebuild the release.' >&2; exit 1; }
  git bundle verify "$DIR/update.bundle" >/dev/null
  [[ "$MODE" != check ]] || { echo 'Preflight OK. No changes made.'; exit 0; }
fi
mkdir -p "$APP/.deployments"
chmod 700 "$APP/.deployments"
exec 9>"$APP/.deployments/crm-learning.lock"
flock -n 9 || { echo 'STOP: another CRM deployment is running.' >&2; exit 1; }
health_basic() {
  local ready=0
  for _ in {1..10}; do
    if systemctl is-active --quiet "$UNIT" && curl --noproxy '*' --fail --silent --show-error --connect-timeout 2 --max-time 3 http://127.0.0.1:8095/ -o /dev/null 2>/dev/null; then ready=1; break; fi
    sleep 1
  done
  [[ "$ready" == 1 ]]
}
health_release() {
  health_basic || return 1
  local expected path actual code
  while read -r expected path; do
    actual=$(curl --noproxy '*' --fail --silent --show-error --connect-timeout 2 --max-time 5 "http://127.0.0.1:8095/static/$path" | sha256sum | cut -d' ' -f1) || return 1
    [[ "$actual" == "$expected" ]] || return 1
  done < "$DIR/health.tsv"
  code=$(curl --noproxy '*' --silent --show-error --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8095/api/learning/context) || return 1
  [[ "$code" == 200 || "$code" == 401 || "$code" == 403 ]]
}
rollback_code() {
  [[ $(git rev-parse HEAD) == "$TARGET" ]] || { echo 'Rollback stopped: HEAD changed.' >&2; return 1; }
  git diff --quiet && git diff --cached --quiet || { echo 'Rollback stopped: tracked files changed.' >&2; return 1; }
  git apply --index --check "$DIR/rollback.patch" || return 1
  (umask 022; git apply --index --binary "$DIR/rollback.patch") || return 1
  git -c user.name='Averon deploy' -c user.email='deploy@localhost' commit -m "Rollback CRM learning release ${TARGET:0:7}" || return 1
  systemctl restart "$UNIT" || return 1
  health_basic || return 1
  echo 'Previous CRM code restored by a new commit; database and settings unchanged.'
}
if [[ "$MODE" == rollback ]]; then rollback_code; exit; fi
[[ $(git rev-parse HEAD) == "$BASE" ]] || { echo 'STOP: production changed during preflight.' >&2; exit 1; }
STATE=$(mktemp -d "$APP/.deployments/crm-learning-$(date -u +%Y%m%d-%H%M%S)-XXXXXX")
chmod 700 "$STATE"
printf '%s\n' "$BASE" > "$STATE/base-commit"
git branch --show-current > "$STATE/branch"
systemctl show "$UNIT" -p MainPID -p ActiveState -p ExecMainStartTimestamp > "$STATE/crm-before.txt"
# Only changed, already-tracked source files. No .env, webphone.json, DB or logs.
tar --create --gzip --file "$STATE/source-before.tar.gz" --null --verbatim-files-from --files-from "$DIR/before-files.zlist"
printf 'Backup: %s\n' "$STATE"
git fetch --no-tags "$DIR/update.bundle" "$SOURCE_REF"
[[ $(git rev-parse FETCH_HEAD) == "$TARGET" ]] || { echo 'STOP: bundle target mismatch.' >&2; exit 1; }
# Git refuses to overwrite tracked edits or colliding untracked files. No reset/force.
(umask 022; git merge --ff-only --no-edit "$TARGET")
on_failure() {
  local status=$?
  trap - ERR
  echo 'Deployment verification failed; restoring the previous CRM code.' >&2
  if rollback_code; then echo 'rolled-back' > "$STATE/result"; else echo 'rollback-needs-review' > "$STATE/result"; echo "Rollback did not finish. Backup: $STATE" >&2; fi
  exit "$status"
}
trap on_failure ERR
systemctl restart "$UNIT"
health_release
systemctl show "$UNIT" -p MainPID -p ActiveState -p ExecMainStartTimestamp > "$STATE/crm-after.txt"
printf '%s\n' "$TARGET" > "$STATE/deployed-commit"
printf 'deployed-local-checks-ok\n' > "$STATE/result"
trap - ERR
printf 'CRM updated: %s\n' "${TARGET:0:7}"
echo 'Only callbot-crm.service restarted. No migrations or settings changes.'
echo 'Open https://averonleads.ru and refresh with Ctrl+F5.'
# A public check cannot undo a healthy local release solely because outbound DNS is unavailable.
if curl --fail --silent --show-error --connect-timeout 3 --max-time 10 https://averonleads.ru/ -o /dev/null; then echo 'Public HTTPS check: OK.'; else echo 'Public HTTPS check unavailable; verify the site from your browser.' >&2; fi
