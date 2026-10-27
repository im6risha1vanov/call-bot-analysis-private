#!/usr/bin/env bash
# Телефон в браузере (crm/webphone.py): менеджер звонит прямо из вкладки CRM, без Talker.
# Путь звонка: браузер -> наш сайт /webphone/ws -> туннель робота (127.0.0.1:8088) -> Asterisk на VPS
# -> Mango от SIP-учётки добавочного менеджера. Для Mango это звонок менеджера: запись и разбор как обычно.
# Робота не трогаем: наши настройки лежат в отдельных файлах Asterisk (pjsip_webphone.conf, extensions_webphone.conf).
#
# Запуск на основном сервере (root), из /opt/callbot-astra:
#   bash scripts/webphone_setup.sh check        — что готово: туннель, модули WebRTC в Asterisk, подключённые добавочные (ничего не меняет)
#   bash scripts/webphone_setup.sh build        — один раз: пересобрать Asterisk на VPS с шифрованием звука для браузера (~30 мин),
#                                                 Asterisk перезапустится сам, когда у робота не будет звонков
#   bash scripts/webphone_setup.sh build-status — как идёт пересборка
#   bash scripts/webphone_setup.sh add 10       — подключить добавочный: спросит SIP-логин и пароль его учётки из кабинета Mango
#   bash scripts/webphone_setup.sh remove 10    — отключить добавочный
#   bash scripts/webphone_setup.sh apply        — заново записать настройки на VPS (если робот пересоздал конфиги Asterisk)
#   bash scripts/webphone_setup.sh off          — выключить телефон в браузере совсем (Talker работает как раньше)
set -euo pipefail
trap 'echo "ошибка в строке $LINENO: $BASH_COMMAND"' ERR
APP=/opt/callbot-astra
ENV=$APP/.env
DIR=/root/webphone
SECRETS=$DIR/accounts          # доб <tab> логин <tab> домен <tab> пароль Mango <tab> пароль браузера; только root
CRM_FILE=$APP/webphone.json     # то, что видит CRM: пароли браузера, без паролей Mango
KEY=/root/.ssh/voice_relay
cd "$APP"

getenv() { { grep -m1 "^$1=" "$ENV" || true; } | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//"; }
vps_ip() { grep -o 'root@[0-9.]*' /etc/systemd/system/callbot-voice-relay.service 2>/dev/null | head -1 | cut -d@ -f2; }
VPS=$(vps_ip)
[ -n "$VPS" ] || { echo "не нашёл VPS робота (юнит callbot-voice-relay). Телефону в браузере нужен тот же Asterisk, что и роботу."; exit 1; }
vps() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 "root@$VPS" "$@"; }
ast() { vps "asterisk -rx '$1'"; }

umask 077
mkdir -p "$DIR"
touch "$SECRETS"

write_crm_file() {
  python3 - "$SECRETS" "$CRM_FILE" <<'PY'
import json, sys
out = {}
for line in open(sys.argv[1]):
    parts = line.rstrip('\n').split('\t')
    if len(parts) == 5 and parts[0].isdigit():
        out[parts[0]] = {'user': 'wp' + parts[0], 'password': parts[4]}
open(sys.argv[2], 'w').write(json.dumps(out, ensure_ascii=False, indent=1))
PY
  chown callbot-astra: "$CRM_FILE" 2>/dev/null || true
  chmod 600 "$CRM_FILE"
}

gen_pjsip() {
  echo '; телефон в браузере: scripts/webphone_setup.sh на основном сервере, файл перезаписывается'
  echo '[transport-ws]'
  echo 'type=transport'
  echo 'protocol=ws'
  echo 'bind=0.0.0.0'
  while IFS=$'\t' read -r ext login domain pass bpass; do
    [ -n "$ext" ] || continue
    cat <<CONF

; доб. $ext: браузер менеджера
[wp$ext]
type=endpoint
transport=transport-ws
context=webphone-$ext
disallow=all
allow=alaw,ulaw
webrtc=yes
dtls_auto_generate_cert=yes
auth=wp$ext
aors=wp$ext

[wp$ext]
type=auth
auth_type=userpass
username=wp$ext
password=$bpass

[wp$ext]
type=aor
max_contacts=5
remove_existing=yes

; доб. $ext: его SIP-учётка в Mango, только исходящие (без регистрации, входящие идут в Talker как раньше)
[wpm$ext]
type=auth
auth_type=userpass
username=$login
password=$pass

[wpm$ext]
type=aor
contact=sip:$domain

[wpm$ext]
type=endpoint
transport=transport-udp
context=voicebot-in
disallow=all
allow=alaw,ulaw
outbound_auth=wpm$ext
aors=wpm$ext
from_user=$login
from_domain=$domain
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
CONF
  done < "$SECRETS"
}

gen_dialplan() {
  echo '; телефон в браузере: scripts/webphone_setup.sh на основном сервере, файл перезаписывается'
  while IFS=$'\t' read -r ext _rest; do
    [ -n "$ext" ] || continue
    cat <<CONF

[webphone-$ext]
; только российские номера 7XXXXXXXXXX, как и в CRM; платные 8-803 и 8-809 не набираем
exten => _7XXXXXXXXXX,1,NoOp(webphone доб.$ext -> \${EXTEN})
 same => n,GotoIf(\$["\${EXTEN:0:4}" = "7803" | "\${EXTEN:0:4}" = "7809"]?deny)
 same => n,Dial(PJSIP/\${EXTEN}@wpm$ext,90)
 same => n,Hangup()
 same => n(deny),Hangup(21)
exten => _X.,1,Hangup(21)
CONF
  done < "$SECRETS"
}

modules_ok() {
  local out
  out=$(ast 'module show like srtp'; ast 'module show like websocket')
  grep -q 'res_srtp.so' <<<"$out" && grep -q 'res_http_websocket.so' <<<"$out" && grep -q 'res_pjsip_transport_websocket.so' <<<"$out"
}

apply() {
  write_crm_file
  gen_pjsip | vps 'umask 027; cat > /etc/asterisk/pjsip_webphone.conf'
  gen_dialplan | vps 'umask 027; cat > /etc/asterisk/extensions_webphone.conf'
  vps 'set -e; cd /etc/asterisk
    grep -q "^#tryinclude pjsip_webphone.conf" pjsip.conf || printf "\n#tryinclude pjsip_webphone.conf\n" >> pjsip.conf
    grep -q "^#tryinclude extensions_webphone.conf" extensions.conf || printf "\n#tryinclude extensions_webphone.conf\n" >> extensions.conf
    chown asterisk: pjsip_webphone.conf extensions_webphone.conf 2>/dev/null || true'
  if ! ast 'pjsip show transports' | grep -q 'transport-ws'; then
    # новый транспорт Asterisk подхватывает только при запуске; ждём, пока у робота не будет звонков
    ast 'core restart when convenient' >/dev/null
    echo "Asterisk перезапустится, как только у робота не будет звонков (обычно сразу)."
    sleep 12
  else
    ast 'module reload res_pjsip.so' >/dev/null
    ast 'dialplan reload' >/dev/null
  fi
  echo "добавочные в браузере: $(cut -f1 "$SECRETS" | tr '\n' ' ')"
  modules_ok || echo "ВНИМАНИЕ: в Asterisk нет модулей WebRTC. Сначала: bash scripts/webphone_setup.sh build"
}

case "${1:-check}" in
  check)
    echo "VPS: $VPS"
    echo "туннель робота: $(systemctl is-active callbot-voice-relay)"
    echo "Asterisk отвечает на /ws через туннель: HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/ws || true) (ожидается 400 или 426)"
    if modules_ok; then echo "модули WebRTC: есть"; else echo "модули WebRTC: НЕТ, нужен build"; fi
    echo "адрес VPS снаружи: $(vps 'curl -4 -s --max-time 5 https://ifconfig.me || true'), на интерфейсе: $(vps 'hostname -I' | awk '{print $1}')"
    echo "подключены в CRM: $(cut -f1 "$SECRETS" | tr '\n' ' ')"
    ast 'pjsip show endpoints' | grep -E 'Endpoint: +wp' || echo "в Asterisk добавочных браузера нет"
    ;;
  add)
    EXT=${2:?"укажи добавочный: bash scripts/webphone_setup.sh add 10"}
    [[ "$EXT" =~ ^[0-9]+$ ]] || { echo "добавочный — только цифры"; exit 1; }
    DOMAIN=$(getenv MANGO_SIP_DOMAIN)
    echo "SIP-логин учётки добавочного $EXT из кабинета Mango (Сотрудники -> сотрудник -> SIP-учётки),"
    read -r -p "например user10@${DOMAIN:-vpbxXXXX.mangosip.ru}: " LOGIN
    LOGIN=${LOGIN#sip:}
    if [[ "$LOGIN" == *@* ]]; then DOMAIN=${LOGIN#*@}; LOGIN=${LOGIN%%@*}; fi
    [ -n "$LOGIN" ] && [ -n "$DOMAIN" ] || { echo "нужен логин вида user@домен"; exit 1; }
    read -r -s -p "пароль этой SIP-учётки (на экран не выводится): " PASS; echo
    [ -n "$PASS" ] || { echo "пароль пустой"; exit 1; }
    case "$LOGIN$PASS$DOMAIN" in *$'\t'*) echo "в логине или пароле есть табуляция"; exit 1;; esac
    BPASS=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
    grep -v "^$EXT	" "$SECRETS" > "$SECRETS.new" || true
    printf '%s\t%s\t%s\t%s\t%s\n' "$EXT" "$LOGIN" "$DOMAIN" "$PASS" "$BPASS" >> "$SECRETS.new"
    mv "$SECRETS.new" "$SECRETS"
    apply
    echo "готово: менеджер с доб. $EXT обновляет страницу CRM и в карточке номера выбирает «Звонить: из браузера»."
    ;;
  remove)
    EXT=${2:?"укажи добавочный"}
    grep -v "^$EXT	" "$SECRETS" > "$SECRETS.new" || true
    mv "$SECRETS.new" "$SECRETS"
    apply
    ;;
  apply)
    apply
    ;;
  off)
    : > "$SECRETS"
    write_crm_file
    vps 'rm -f /etc/asterisk/pjsip_webphone.conf /etc/asterisk/extensions_webphone.conf'
    ast 'module reload res_pjsip.so' >/dev/null || true
    ast 'dialplan reload' >/dev/null || true
    echo "телефон в браузере выключен, у всех снова Talker."
    ;;
  build)
    if modules_ok; then echo "модули WebRTC уже есть, пересборка не нужна."; exit 0; fi
    vps 'cat > /root/webphone-build.sh' <<'BUILD'
#!/usr/bin/env bash
# Пересборка Asterisk с libsrtp (шифрование звука WebRTC). Запущено из webphone_setup.sh build.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "== $(date) старт"
apt-get update -qq
apt-get install -y -qq build-essential curl pkg-config libedit-dev libjansson-dev libxml2-dev uuid-dev \
  libsqlite3-dev libssl-dev libsrtp2-dev >/dev/null
SRC=/usr/src/asterisk-build
if [ ! -x "$SRC/configure" ]; then
  rm -rf "$SRC" && mkdir -p "$SRC" && cd "$SRC"
  curl -fsSL -o src.tar.gz https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-22-current.tar.gz \
    || curl -fsSL -o src.tar.gz https://github.com/asterisk/asterisk/archive/refs/tags/22.11.0.tar.gz
  tar xzf src.tar.gz --strip-components=1
fi
cd "$SRC"
make -s distclean >/dev/null 2>&1 || true
./configure --quiet
grep -q 'HAVE_SRTP 1' include/asterisk/autoconfig.h || { echo "configure не нашёл libsrtp"; exit 1; }
make -s menuselect.makeopts >/dev/null
menuselect/menuselect --disable BUILD_NATIVE --enable res_srtp --disable-category MENUSELECT_CORE_SOUNDS \
  --disable-category MENUSELECT_MOH --disable-category MENUSELECT_EXTRA_SOUNDS menuselect.makeopts >/dev/null
echo "== $(date) сборка"
make -s -j"$(nproc)" >/dev/null 2>&1
make -s install >/dev/null 2>&1
ldconfig
echo "== $(date) установлено, перезапуск Asterisk, когда у робота не будет звонков"
asterisk -rx 'core restart when convenient' || systemctl restart asterisk
sleep 20
asterisk -rx 'module show like srtp'
echo "== $(date) готово"
BUILD
    vps 'systemctl reset-failed webphone-build 2>/dev/null || true; systemd-run --unit=webphone-build --collect bash -c "bash /root/webphone-build.sh > /root/webphone-build.log 2>&1"'
    echo "пересборка запущена на VPS (около 30 минут). Ход: bash scripts/webphone_setup.sh build-status"
    ;;
  build-status)
    vps 'systemctl is-active webphone-build 2>/dev/null || true; tail -5 /root/webphone-build.log 2>/dev/null || echo "лога нет"'
    if modules_ok; then echo "модули WebRTC: есть"; else echo "модули WebRTC: пока нет"; fi
    ;;
  *)
    sed -n '2,16p' "$0"; exit 1;;
esac
