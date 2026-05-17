#!/usr/bin/env bash
# wizard.sh — Infrabox installation wizard
#
# Usage:
#   bash wizard.sh [host-report.json]   → interactive wizard → topology.yml
#
# Reads host-report.json (output of probe.sh), asks questions with
# auto-detected defaults, generates topology.yml.
#
# Requires: jq or python3 (for JSON parsing)

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'
R='\033[0;31m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗ ПОМИЛКА:${N} $*" >&2; exit 1; }
hdr()  { echo -e "\n${C}━━━ $* ━━━${N}"; }

# ── Args ──────────────────────────────────────────────────────────────────────
REPORT="${1:-host-report.json}"
OUT="topology.yml"

[ -f "$REPORT" ] || fail "Не знайдено: $REPORT\nЗапустіть спочатку: bash probe.sh"

# ── JSON getter ───────────────────────────────────────────────────────────────
# Supports jq or python3 (one of them must be present)
_has_jq=0
_has_py=0
command -v jq      &>/dev/null && _has_jq=1
command -v python3 &>/dev/null && _has_py=1
[ "$_has_jq" = "0" ] && [ "$_has_py" = "0" ] && fail "Потрібен jq або python3"

_get() {
    local path="$1"
    if [ "$_has_jq" = "1" ]; then
        jq -r "${path} // empty" "$REPORT" 2>/dev/null || true
    else
        # python3 fallback — path like .network.hostname or .interfaces[0].ip
        local rfile="$REPORT"
        python3 - "$path" "$rfile" 2>/dev/null <<'PY'
import sys, json
data = json.load(open(sys.argv[2]))
raw  = sys.argv[1].lstrip('.').split('.')
v = data
try:
    for k in raw:
        if '[' in k:
            name, rest = k.split('[', 1)
            idx = int(rest.rstrip(']'))
            v = v[name][idx]
        else:
            v = v[k]
    print('' if v is None else str(v))
except Exception:
    print('')
PY
    fi
}

# ── Interactive prompts ───────────────────────────────────────────────────────
ask() {
    # ask "Prompt text" "default value" VAR_NAME
    local prompt="$1" default="$2" varname="$3" result=""
    if [ -n "$default" ]; then
        printf "  ${B}%s${N} [${default}]: " "$prompt"
    else
        printf "  ${B}%s${N}: " "$prompt"
    fi
    read -r result || true
    result="${result:-$default}"
    while [ -z "$result" ]; do
        printf "  (обов'язково) %s: " "$prompt"
        read -r result || true
    done
    printf -v "$varname" '%s' "$result"
}

ask_yn() {
    # ask_yn "Prompt" y|n VAR_NAME  → sets 1 or 0
    local prompt="$1" default="${2:-y}" varname="$3" opts ans=""
    [ "$default" = "y" ] && opts="Y/n" || opts="y/N"
    printf "  ${B}%s${N} [%s]: " "$prompt" "$opts"
    read -r ans || true
    ans="${ans:-$default}"
    local ans_l
    ans_l=$(echo "$ans" | tr '[:upper:]' '[:lower:]')
    case "$ans_l" in
        y|yes|т|так) printf -v "$varname" '%s' "1" ;;
        *)            printf -v "$varname" '%s' "0" ;;
    esac
}

# ── Read probe data ───────────────────────────────────────────────────────────
P_HOSTNAME=$(_get '.network.hostname')
P_FQDN=$(_get     '.network.fqdn')
P_USER=$(_get     '.user.name')
P_ARCH=$(_get     '.hardware.arch')
P_OS_ID=$(_get    '.os.id')
P_OS_VER=$(_get   '.os.version')
P_OS_PRETTY=$(_get '.os.pretty')
P_DOCKER=$(_get   '.software.docker')
P_PLATFORM=$(_get '.os.platform')
P_RAM=$(_get      '.hardware.ram_mb')

# First non-loopback IP (strip CIDR if present)
P_IP=$(_get '.network.interfaces[0].ip' | cut -d/ -f1 || true)
P_IP="${P_IP:-unknown}"

# Port occupancy — warn about conflicts
_port_status() {
    # returns "free" or process name
    if [ "$_has_jq" = "1" ]; then
        local free proc
        free=$(jq -r ".ports.\"$1\".free // true" "$REPORT" 2>/dev/null || echo "true")
        if [ "$free" = "false" ]; then
            proc=$(jq -r ".ports.\"$1\".process // \"?\"" "$REPORT" 2>/dev/null || echo "?")
            echo "BUSY:${proc}"
        else
            echo "free"
        fi
    else
        echo "free"   # skip check without jq (non-critical)
    fi
}

# Map probe arch → docker/topology arch string
case "${P_ARCH:-}" in
    x86_64)  NODE_ARCH="x86_64" ;;
    armv7l)  NODE_ARCH="arm/v7" ;;
    aarch64) NODE_ARCH="arm64"  ;;
    *)       NODE_ARCH="${P_ARCH:-unknown}" ;;
esac

# ── Welcome ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${C}║          Infrabox  Installation  Wizard              ║${N}"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"
echo ""
printf "  Хост:   ${G}%s${N}  (%s)  arch: %s\n" \
    "${P_HOSTNAME:-?}" "${P_IP}" "${NODE_ARCH}"
printf "  ОС:     ${G}%s${N}\n" "${P_OS_PRETTY:-${P_OS_ID:-?}}"
printf "  Docker: %s\n" "${P_DOCKER:-не встановлено}"
printf "  RAM:    %s MB\n" "${P_RAM:-?}"
echo ""
echo "  Натисніть Enter щоб прийняти значення в дужках."

# ── NODE ─────────────────────────────────────────────────────────────────────
hdr "Вузол"

ask "Псевдонім вузла (ключ у topology)"  "${P_HOSTNAME:-node1}"             NODE_ALIAS
ask "Host або IP"                        "${P_FQDN:-${P_HOSTNAME}.local}"   NODE_HOST
ask "SSH-користувач"                     "${P_USER:-admin}"                 NODE_USER
ask "SSH-ключ (шлях на машині адміна)"   "~/.ssh/id_ed25519"                NODE_SSH_KEY
ask "Deploy directory"                   "/home/${NODE_USER}/infrabox"       NODE_DEPLOY_DIR

NODE_OS="${P_OS_ID:-linux}/${P_OS_VER:-unknown}"
ok "Arch: ${NODE_ARCH}  |  OS: ${NODE_OS}"

# ── SYSTEM ───────────────────────────────────────────────────────────────────
hdr "Система"

ask "Назва системи"   "Infrabox"                              SYS_NAME
ask "Опис"            "Smart home monitoring"                 SYS_DESC
ask "Timezone"        "Europe/Kyiv"                           SYS_TZ
ask "Git repo"        "https://github.com/shura105/infrabox.git" SYS_REPO
ask "Git branch"      "main"                                  SYS_BRANCH

# ── SUBSYSTEMS ───────────────────────────────────────────────────────────────
hdr "Підсистеми"

echo -e "  ${G}core${N} — завжди (Redis, MQTT, auth, core, simulator, selfdiagnostic)"
ask_yn "ui   — frontend + backend API"       "y" DEPLOY_UI
ask_yn "arch — архіватор (SQLite трендів)"   "y" DEPLOY_ARCH
ask_yn "adm  — адміністративний сервіс"      "y" DEPLOY_ADM

# ── PORT CONFLICT CHECK ───────────────────────────────────────────────────────
hdr "Перевірка портів"

_check_port() {
    local p="$1" label="$2"
    local st
    st=$(_port_status "$p")
    if [ "$st" = "free" ]; then
        ok "Port ${p} (${label}) — вільний"
    else
        warn "Port ${p} (${label}) зайнятий: ${st#BUSY:}"
    fi
}

_check_port 1883 "MQTT real"
_check_port 1884 "MQTT sim"
[ "$DEPLOY_UI"   = "1" ] && _check_port 80  "HTTP"
[ "$DEPLOY_UI"   = "1" ] && _check_port 443 "HTTPS"

# ── SECURITY ─────────────────────────────────────────────────────────────────
hdr "Безпека"

if command -v python3 &>/dev/null; then
    DEFAULT_JWT=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)
fi
DEFAULT_JWT="${DEFAULT_JWT:-$(tr -dc 'a-zA-Z0-9' < /dev/urandom 2>/dev/null | head -c 64 || true)}"
DEFAULT_JWT="${DEFAULT_JWT:-CHANGE_ME_$(date +%s)}"

printf "  ${B}JWT_SECRET${N} [авто: ${G}%.16s${N}…]: " "$DEFAULT_JWT"
read -r jwt_input || true
JWT_SECRET="${jwt_input:-$DEFAULT_JWT}"

ask "JWT expire (годин)"  "24"                  JWT_EXPIRE
ask "DROP_ID"             "${NODE_ALIAS}"        DROP_ID

# ── SSL ──────────────────────────────────────────────────────────────────────
hdr "SSL"

echo "  1) mkcert      — trusted CA, рекомендовано"
echo "  2) self-signed — openssl (браузер попередить)"
echo "  3) skip        — без HTTPS"
printf "  ${B}Вибір${N} [1]: "
read -r ssl_pick || true
ssl_pick="${ssl_pick:-1}"
case "$ssl_pick" in
    1) SSL_MODE="mkcert"     ;;
    2) SSL_MODE="selfsigned" ;;
    3) SSL_MODE="skip"       ;;
    *) SSL_MODE="mkcert"     ;;
esac

SSL_HOST=""
if [ "$SSL_MODE" != "skip" ]; then
    ask "Hostname для сертифіката" "$NODE_HOST" SSL_HOST
fi

# ── CONFIRMATION ─────────────────────────────────────────────────────────────
echo ""
echo -e "${C}━━━ Підсумок ━━━${N}"
echo ""
printf "  Вузол:      %s → %s\n"       "$NODE_ALIAS" "$NODE_HOST"
printf "  SSH:        %s@%s  %s\n"     "$NODE_USER" "$NODE_HOST" "$NODE_SSH_KEY"
printf "  Deploy dir: %s\n"             "$NODE_DEPLOY_DIR"
printf "  OS / Arch:  %s / %s\n"       "$NODE_OS" "$NODE_ARCH"
printf "  Система:    %s — %s\n"       "$SYS_NAME" "$SYS_DESC"

SUBS_LIST="core"
[ "$DEPLOY_UI"   = "1" ] && SUBS_LIST="${SUBS_LIST}, ui"
[ "$DEPLOY_ARCH" = "1" ] && SUBS_LIST="${SUBS_LIST}, arch"
[ "$DEPLOY_ADM"  = "1" ] && SUBS_LIST="${SUBS_LIST}, adm"
printf "  Підсистеми: %s\n" "$SUBS_LIST"
printf "  JWT:        %.16s…  expire: %sh\n" "$JWT_SECRET" "$JWT_EXPIRE"
printf "  DROP_ID:    %s\n" "$DROP_ID"
printf "  SSL:        %s%s\n" "$SSL_MODE" \
    "$([ -n "$SSL_HOST" ] && printf ' → %s' "$SSL_HOST" || true)"
echo ""

[ -f "$OUT" ] && warn "$OUT вже існує — буде перезаписано"

ask_yn "Записати topology.yml?" "y" DO_WRITE
if [ "$DO_WRITE" = "0" ]; then
    warn "Скасовано. topology.yml не записано."
    exit 0
fi

# ── GENERATE: open file ───────────────────────────────────────────────────────
GEN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$OUT" <<TOPO
# topology.yml — карта розгортання Infrabox
# Згенеровано wizard.sh ${GEN_TS}
# Редагуйте вручну або через wizard при змінах.
# УВАГА: містить JWT_SECRET — зберігати в безпечному місці.

version: "1"

# ── Система ──────────────────────────────────────────────────────────────────

system:
  name:        "${SYS_NAME}"
  description: "${SYS_DESC}"
  timezone:    "${SYS_TZ}"
  repo:        "${SYS_REPO}"
  branch:      "${SYS_BRANCH}"

# ── Вузли ────────────────────────────────────────────────────────────────────

nodes:
  ${NODE_ALIAS}:
    host:        "${NODE_HOST}"
    user:        "${NODE_USER}"
    ssh_key:     "${NODE_SSH_KEY}"
    arch:        "${NODE_ARCH}"
    os:          "${NODE_OS}"
    deploy_dir:  "${NODE_DEPLOY_DIR}"
    role:        "primary"

# ── Підсистеми ────────────────────────────────────────────────────────────────

subsystems:

  core:
    node:     "${NODE_ALIAS}"
    workdir:  "core"
    description: "Redis, MQTT-брокери, обробка точок, авторизація, симулятор, selfdiagnostic"
    containers:
      - infrabox-redis
      - infrabox-mosquitto-real
      - infrabox-mosquitto-sim
      - infrabox-auth
      - infrabox-core
      - infrabox-simulator
      - infrabox-selfdiagnostic
    ports_exposed:
      - { host: 1883, container: 1883, service: "mosquitto-real", proto: "mqtt" }
      - { host: 1884, container: 1883, service: "mosquitto-sim",  proto: "mqtt" }
    env:
      JWT_SECRET:       "${JWT_SECRET}"
      JWT_EXPIRE_HOURS: "${JWT_EXPIRE}"
      DROP_ID:          "${DROP_ID}"
TOPO

# ── ui subsystem (optional) ───────────────────────────────────────────────────
if [ "$DEPLOY_UI" = "1" ]; then
cat >> "$OUT" <<TOPO

  ui:
    node:     "${NODE_ALIAS}"
    workdir:  "ui"
    description: "Frontend (nginx+HTTPS), backend API, WebSocket"
    containers:
      - infrabox-web
      - infrabox-backend
    ports_exposed:
      - { host: 80,  container: 80,  service: "web", proto: "http"  }
      - { host: 443, container: 443, service: "web", proto: "https" }
TOPO
fi

# ── arch subsystem (optional) ─────────────────────────────────────────────────
if [ "$DEPLOY_ARCH" = "1" ]; then
cat >> "$OUT" <<TOPO

  arch:
    node:     "${NODE_ALIAS}"
    workdir:  "arch"
    description: "Архіватор: запис історії, backend та UI для трендів"
    containers:
      - infrabox-arch
      - infrabox-arch-backend
      - infrabox-arch-ui
    ports_exposed: []
TOPO
fi

# ── adm subsystem (optional) ──────────────────────────────────────────────────
if [ "$DEPLOY_ADM" = "1" ]; then
cat >> "$OUT" <<TOPO

  adm:
    node:     "${NODE_ALIAS}"
    workdir:  "adm"
    description: "Адміністративний сервіс: контейнери, хост, конфігурація"
    containers:
      - infrabox-adm
    ports_exposed: []
TOPO
fi

# ── Docker network + deploy order ─────────────────────────────────────────────
cat >> "$OUT" <<'TOPO'

# ── Docker-мережа ─────────────────────────────────────────────────────────────

docker_network:
  name:   "infrabox-net"
  driver: "bridge"

# ── Порядок розгортання ───────────────────────────────────────────────────────

deploy_order:
TOPO

echo "  - core" >> "$OUT"
[ "$DEPLOY_ARCH" = "1" ] && echo "  - arch" >> "$OUT"
[ "$DEPLOY_UI"   = "1" ] && echo "  - ui"   >> "$OUT"
[ "$DEPLOY_ADM"  = "1" ] && echo "  - adm"  >> "$OUT"

cat >> "$OUT" <<'TOPO'

undeploy_order:
TOPO

[ "$DEPLOY_ADM"  = "1" ] && echo "  - adm"  >> "$OUT"
[ "$DEPLOY_UI"   = "1" ] && echo "  - ui"   >> "$OUT"
[ "$DEPLOY_ARCH" = "1" ] && echo "  - arch" >> "$OUT"
echo "  - core" >> "$OUT"

# ── Data section ──────────────────────────────────────────────────────────────
cat >> "$OUT" <<'TOPO'

# ── Дані ─────────────────────────────────────────────────────────────────────

data:
  - type:        "docker_volume"
    name:        "core_redis-data"
    description: "Redis AOF дані (points, стан системи)"
    critical:    true
    backup_cmd:  "docker run --rm -v core_redis-data:/data -v $(pwd):/backup alpine tar czf /backup/redis-backup.tar.gz /data"

  - type:        "bind"
    path:        "core/mosquitto/mosquitto-real/data"
    description: "MQTT real broker persistence"
    critical:    false

  - type:        "bind"
    path:        "core/mosquitto/mosquitto-sim/data"
    description: "MQTT sim broker persistence"
    critical:    false
TOPO

if [ "$DEPLOY_ARCH" = "1" ]; then
cat >> "$OUT" <<'TOPO'

  - type:        "bind"
    path:        "arch/archivator/data"
    description: "Архів значень параметрів (SQLite томи)"
    critical:    true
TOPO
fi

if [ "$DEPLOY_UI" = "1" ]; then
cat >> "$OUT" <<'TOPO'

  - type:        "bind"
    path:        "ui/data"
    description: "Конфігурація UI: project.json, screens/*.json"
    critical:    true
TOPO
fi

if [ "$DEPLOY_ADM" = "1" ]; then
cat >> "$OUT" <<'TOPO'

  - type:        "bind"
    path:        "adm/data"
    description: "Дані адмін-сервісу: subsystems.json"
    critical:    false
TOPO
fi

cat >> "$OUT" <<'TOPO'

  - type:        "bind"
    path:        "log"
    description: "Спільні логи всіх підсистем"
    critical:    false
TOPO

# ── SSL section (optional) ────────────────────────────────────────────────────
if [ "$SSL_MODE" != "skip" ]; then
cat >> "$OUT" <<TOPO

# ── SSL ──────────────────────────────────────────────────────────────────────

ssl:
  mode:      "${SSL_MODE}"
  hostname:  "${SSL_HOST}"
  cert:      "ui/frontend/ssl/infrabox.crt"
  key:       "ui/frontend/ssl/infrabox.key"
TOPO
fi

echo "" >> "$OUT"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  topology.yml записано ✓${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
echo "  Файл:      $(pwd)/${OUT}"
echo "  Підсистем: ${SUBS_LIST}"
echo ""
echo "Наступний крок:"
echo "  1. Запустіть probe.sh на цільовому хості → host-report.json"
echo "  2. bash host-prep.sh --deploy-dir ${NODE_DEPLOY_DIR}"
echo "  3. bash deploy.sh"
