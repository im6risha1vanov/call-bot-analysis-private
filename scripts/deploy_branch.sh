#!/usr/bin/env bash
# Выкладка ветки на сервер одной командой, без кавычек в PowerShell:
#   cd /opt/callbot-astra && git -c safe.directory=/opt/callbot-astra fetch private-backup && \
#   git -c safe.directory=/opt/callbot-astra show private-backup/<ветка>:scripts/deploy_branch.sh | bash -s <ветка>
# Останавливается, если на сервере есть незакоммиченные правки. Конфликт только в метках ?v= файла
# crm/static/index.html решает в пользу ветки (у неё те же строки и новые метки), любой другой — отменяет merge.
# Первая строка вывода — версия для отката: git reset --hard <она> && bash scripts/crm_setup.sh update
set -euo pipefail
BRANCH=${1:?укажите ветку}
cd /opt/callbot-astra
g() { git -c safe.directory=/opt/callbot-astra -c user.name=deploy -c user.email=deploy@localhost "$@"; }
echo "откат: $(g rev-parse --short HEAD)"
if ! g diff --quiet || ! g diff --cached --quiet; then
  echo 'СТОП: на сервере есть незакоммиченные правки:'; g status --short; exit 1
fi
if ! g merge --no-edit "private-backup/$BRANCH"; then
  conflicts=$(g diff --name-only --diff-filter=U)
  if [ "$conflicts" = crm/static/index.html ]; then
    g checkout --theirs crm/static/index.html
    g add crm/static/index.html
    g commit --no-edit -q
    echo 'index.html: взяты метки ветки'
  else
    echo "СТОП: конфликт в $conflicts, merge отменён"; g merge --abort; exit 1
  fi
fi
g log --oneline -1
g status --short
bash scripts/crm_setup.sh update
