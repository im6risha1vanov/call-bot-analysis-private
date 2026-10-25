#!/usr/bin/env bash
# Выкладка ветки на сервер одной командой, без кавычек в PowerShell:
#   cd /opt/callbot-astra && git -c safe.directory=/opt/callbot-astra fetch private-backup && \
#   git -c safe.directory=/opt/callbot-astra show private-backup/<ветка>:scripts/deploy_branch.sh | bash -s <ветка>
# Останавливается, если на сервере есть незакоммиченные правки. Конфликт только в метках ?v= файла
# crm/static/index.html решает в пользу ветки (новые метки), сохраняя файлы, которые подключили другие ветки;
# любой другой конфликт — отменяет merge.
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
    # берём файл ветки, но строки подключения файлов, которых в ней нет (их добавили другие ветки), возвращаем
    g show :2:crm/static/index.html > /tmp/index.ours.html
    g checkout --theirs crm/static/index.html
    stamp=$(grep -o 'v=[0-9a-z]*' crm/static/index.html | head -1)
    while IFS= read -r line; do
      file=$(printf '%s' "$line" | grep -o '/static/[^?]*')
      if [ -n "$file" ] && ! grep -qF "$file?" crm/static/index.html; then
        printf '%s\n' "$line" | sed "s/v=[0-9a-z]*/$stamp/" > /tmp/index.line
        sed -i '/<\/head>/e cat /tmp/index.line' crm/static/index.html
        echo "index.html: вернул строку $file"
      fi
    done < <(grep -E '<(script|link) ' /tmp/index.ours.html)
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
