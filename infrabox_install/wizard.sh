#!/usr/bin/env bash
# wizard.sh — майстер налаштування Infrabox (мультихост)
#
# Usage:
#   bash wizard.sh                          → host-report.json (якщо є) + інтерактив
#   bash wizard.sh r1.json r2.json ...      → кілька probe-звітів, по вузлу на кожен
#
# Гібрид: probe-звіт дає дефолти, користувач підтверджує/редагує + може
# додати вузли вручну. Далі — розподіл підсистем (core/ui/arch/adm) по вузлах.
# Результат: topology.yml.
#
# Requires: python3 (jq не обов'язковий).

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'
R='\033[0;31m'; D='\033[2m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗ ПОМИЛКА:${N} $*" >&2; exit 1; }
hdr()  { echo -e "\n${C}━━━ $* ━━━${N}"; }

OUT="topology.yml"

# ── Args: список probe-звітів ─────────────────────────────────────────────────
REPORTS=()
if [ $# -gt 0 ]; then
    for a in "$@"; do
        [ -f "$a" ] || fail "Не знайдено: $a"
        REPORTS+=("$a")
    done
elif [ -f "host-report.json" ]; then
    REPORTS+=("host-report.json")
fi

command -v python3 &>/dev/null || fail "Потрібен python3"
command -v jq &>/dev/null && HAS_JQ=1 || HAS_JQ=0

# ── JSON getter: _get <file> <path> ───────────────────────────────────────────
_get() {
    local file="$1" path="$2"
    if [ "$HAS_JQ" = "1" ]; then
        jq -r "${path} // empty" "$file" 2>/dev/null || true
    else
        python3 - "$path" "$file" 2>/dev/null <<'PY'
import sys, json
data = json.load(open(sys.argv[2]))
raw  = sys.argv[1].lstrip('.').split('.')
v = data
try:
    for k in raw:
        if '[' in k:
            name, rest = k.split('[', 1)
            v = v[name][int(rest.rstrip(']'))]
        else:
            v = v[k]
    print('' if v is None else str(v))
except Exception:
    print('')
PY
    fi
}

# ── Prompts ───────────────────────────────────────────────────────────────────
ask() {
    local prompt="$1" default="$2" varname="$3" result=""
    if [ -n "$default" ]; then printf "  ${B}%s${N} [${default}]: " "$prompt"
    else printf "  ${B}%s${N}: " "$prompt"; fi
    read -r result || true
    result="${result:-$default}"
    while [ -z "$result" ]; do
        printf "  (обов'язково) %s: " "$prompt"
        read -r result || true
    done
    printf -v "$varname" '%s' "$result"
}

ask_yn() {
    local prompt="$1" default="${2:-y}" varname="$3" opts ans=""
    [ "$default" = "y" ] && opts="Y/n" || opts="y/N"
    printf "  ${B}%s${N} [%s]: " "$prompt" "$opts"
    read -r ans || true
    ans="${ans:-$default}"
    case "$(echo "$ans" | tr '[:upper:]' '[:lower:]')" in
        y|yes|т|так) printf -v "$varname" '%s' "1" ;;
        *)            printf -v "$varname" '%s' "0" ;;
    esac
}

_map_arch() {
    case "$1" in
        x86_64)  echo "x86_64" ;;
        armv7l)  echo "arm/v7" ;;
        aarch64) echo "arm64"  ;;
        *)       echo "${1:-unknown}" ;;
    esac
}

# ── Паралельні масиви вузлів ──────────────────────────────────────────────────
ALIASES=(); HOSTS=(); USERS=(); KEYS=(); DIRS=(); ARCHS=(); OSES=()

add_node_interactive() {
    # add_node_interactive [report_file]
    local rep="${1:-}"
    local d_alias d_host d_user d_key d_dir d_arch d_os
    if [ -n "$rep" ]; then
        local p_host p_fqdn p_user p_arch p_osid p_osver p_ram p_docker p_ip
        p_host=$(_get "$rep" '.network.hostname')
        p_fqdn=$(_get "$rep" '.network.fqdn')
        p_user=$(_get "$rep" '.user.name')
        p_arch=$(_get "$rep" '.hardware.arch')
        p_osid=$(_get "$rep" '.os.id')
        p_osver=$(_get "$rep" '.os.version')
        p_ram=$(_get "$rep" '.hardware.ram_mb')
        p_docker=$(_get "$rep" '.readiness.docker_version')
        p_ip=$(_get "$rep" '.network.primary_ip')
        p_mdns=$(_get "$rep" '.network.mdns_name')
        d_alias="${p_host:-node}"
        # пріоритет: mdns (.local) → fqdn з крапкою → hostname.local → IP
        if [ -n "$p_mdns" ]; then d_host="$p_mdns"
        elif echo "$p_fqdn" | grep -q '\.'; then d_host="$p_fqdn"
        elif [ -n "$p_host" ]; then d_host="${p_host}.local"
        else d_host="$p_ip"; fi
        d_user="${p_user:-admin}"
        d_arch="$(_map_arch "$p_arch")"
        d_os="${p_osid:-linux}/${p_osver:-unknown}"
        echo ""
        printf "  ${G}probe:${N} %s (%s) arch:%s os:%s ram:%sMB docker:%s\n" \
            "${p_host:-?}" "${p_ip:-?}" "$d_arch" "${p_osid:-?}" "${p_ram:-?}" "${p_docker:-нема}"
    else
        d_alias="node$(( ${#ALIASES[@]} + 1 ))"
        d_host=""; d_user="admin"; d_arch="unknown"; d_os="linux/unknown"
    fi
    d_key="~/.ssh/id_ed25519"
    d_dir="/home/${d_user}/infrabox"

    local a_alias a_host a_user a_key a_dir
    ask "Псевдонім вузла"        "$d_alias"  a_alias
    ask "Host або IP"            "$d_host"   a_host
    ask "SSH-користувач"         "$d_user"   a_user
    ask "SSH-ключ (на адмінці)"  "$d_key"    a_key
    ask "Deploy directory"       "/home/${a_user}/infrabox"  a_dir

    ALIASES+=("$a_alias"); HOSTS+=("$a_host"); USERS+=("$a_user")
    KEYS+=("$a_key"); DIRS+=("$a_dir"); ARCHS+=("$d_arch"); OSES+=("$d_os")
    ok "Вузол додано: ${a_alias} (${a_host})"
}

# ── Welcome ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${C}║          Infrabox  Installation  Wizard              ║${N}"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"

# ── NODES ─────────────────────────────────────────────────────────────────────
hdr "Вузли"

for rep in "${REPORTS[@]}"; do
    add_node_interactive "$rep"
done

if [ "${#ALIASES[@]}" -eq 0 ]; then
    info "probe-звітів не передано — додайте вузол вручну."
    add_node_interactive ""
fi

while true; do
    echo ""
    ask_yn "Додати ще один вузол (вручну)?" "n" MORE
    [ "$MORE" = "1" ] || break
    add_node_interactive ""
done

echo ""
ok "Вузлів: ${#ALIASES[@]}  (${ALIASES[*]})"

# ── SYSTEM ───────────────────────────────────────────────────────────────────
hdr "Система"
ask "Назва системи"   "Infrabox"                                  SYS_NAME
ask "Опис"            "Smart home monitoring"                     SYS_DESC
ask "Timezone"        "Europe/Kyiv"                               SYS_TZ
ask "Git repo"        "https://github.com/shura105/infrabox.git"  SYS_REPO
ask "Git branch"      "main"                                      SYS_BRANCH

# ── SECURITY ─────────────────────────────────────────────────────────────────
hdr "Безпека"
DEFAULT_JWT=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "CHANGE_ME_$(date +%s)")
printf "  ${B}JWT_SECRET${N} [авто: ${G}%.16s${N}…]: " "$DEFAULT_JWT"
read -r jwt_input || true
JWT_SECRET="${jwt_input:-$DEFAULT_JWT}"
ask "JWT expire (годин)"  "24"                  JWT_EXPIRE
ask "DROP_ID"             "${ALIASES[0]}"       DROP_ID

# ── PLACEMENT ────────────────────────────────────────────────────────────────
hdr "Розміщення підсистем"
echo "  Оберіть вузол для кожної підсистеми."
echo ""

pick_node() {
    # pick_node <sub> <allow_skip 0|1>  → друкує обраний alias (порожньо = пропустити)
    local sub="$1" allow_skip="$2" i choice
    {
        echo ""
        echo "  Підсистема '${sub}':"
        for i in "${!ALIASES[@]}"; do
            printf "    %d) %s (%s)\n" $((i+1)) "${ALIASES[$i]}" "${HOSTS[$i]}"
        done
        [ "$allow_skip" = "1" ] && echo "    0) не встановлювати"
    } >&2
    while true; do
        printf "  Вибір [1]: " >&2
        read -r choice || true
        choice="${choice:-1}"
        if [ "$choice" = "0" ] && [ "$allow_skip" = "1" ]; then echo ""; return; fi
        if [ "$choice" -ge 1 ] 2>/dev/null && [ "$choice" -le "${#ALIASES[@]}" ]; then
            echo "${ALIASES[$((choice-1))]}"; return
        fi
        echo "  Невірний вибір" >&2
    done
}

PLACE_core=$(pick_node "core (Redis, MQTT, auth, simulator, selfdiag)" 0)
PLACE_ui=$(pick_node   "ui (web + backend API)" 1)
PLACE_arch=$(pick_node "arch (архіватор історії)" 1)
PLACE_adm=$(pick_node  "adm (адмін-сервіс)" 1)

[ -z "$PLACE_core" ] && fail "core обов'язковий"

# ── SSL ──────────────────────────────────────────────────────────────────────
hdr "SSL"
echo "  1) mkcert   2) self-signed   3) skip"
printf "  ${B}Вибір${N} [1]: "
read -r ssl_pick || true
case "${ssl_pick:-1}" in
    2) SSL_MODE="selfsigned" ;;
    3) SSL_MODE="skip" ;;
    *) SSL_MODE="mkcert" ;;
esac
SSL_HOST=""
if [ "$SSL_MODE" != "skip" ]; then
    # дефолт — host вузла з ui (або core)
    SSL_DEF=""
    for i in "${!ALIASES[@]}"; do
        [ "${ALIASES[$i]}" = "${PLACE_ui:-$PLACE_core}" ] && SSL_DEF="${HOSTS[$i]}"
    done
    ask "Hostname для сертифіката" "${SSL_DEF}" SSL_HOST
fi

# ── SUMMARY ──────────────────────────────────────────────────────────────────
hdr "Підсумок"
echo ""
for i in "${!ALIASES[@]}"; do
    printf "  Вузол: ${G}%s${N}  %s@%s  %s  [%s]\n" \
        "${ALIASES[$i]}" "${USERS[$i]}" "${HOSTS[$i]}" "${DIRS[$i]}" "${ARCHS[$i]}"
done
echo ""
printf "  core → ${G}%s${N}\n" "$PLACE_core"
printf "  ui   → %s\n" "${PLACE_ui:-—}"
printf "  arch → %s\n" "${PLACE_arch:-—}"
printf "  adm  → %s\n" "${PLACE_adm:-—}"
echo ""
printf "  repo/branch: %s @ %s\n" "$SYS_REPO" "$SYS_BRANCH"
printf "  SSL: %s%s\n" "$SSL_MODE" "$([ -n "$SSL_HOST" ] && printf ' → %s' "$SSL_HOST")"
echo ""

[ -f "$OUT" ] && warn "$OUT вже існує — буде перезаписано"
ask_yn "Записати topology.yml?" "y" DO_WRITE
[ "$DO_WRITE" = "1" ] || { warn "Скасовано."; exit 0; }

# ── GENERATE ──────────────────────────────────────────────────────────────────
GEN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

{
cat <<TOPO
# topology.yml — карта розгортання Infrabox
# Згенеровано wizard.sh ${GEN_TS}
# УВАГА: містить JWT_SECRET — зберігати в безпечному місці.

version: "1"

system:
  name:        "${SYS_NAME}"
  description: "${SYS_DESC}"
  timezone:    "${SYS_TZ}"
  repo:        "${SYS_REPO}"
  branch:      "${SYS_BRANCH}"

nodes:
TOPO

for i in "${!ALIASES[@]}"; do
    ROLE="secondary"; [ "$i" = "0" ] && ROLE="primary"
cat <<TOPO
  ${ALIASES[$i]}:
    host:        "${HOSTS[$i]}"
    user:        "${USERS[$i]}"
    ssh_key:     "${KEYS[$i]}"
    arch:        "${ARCHS[$i]}"
    os:          "${OSES[$i]}"
    deploy_dir:  "${DIRS[$i]}"
    role:        "${ROLE}"
TOPO
done

echo ""
echo "subsystems:"
} > "$OUT"

# emit_sub <name> <node> <workdir> <desc>  + контейнери/порти за типом
emit_sub() {
    local sub="$1" node="$2" workdir="$3" desc="$4"
    [ -z "$node" ] && return
    {
    cat <<TOPO

  ${sub}:
    node:     "${node}"
    workdir:  "${workdir}"
    description: "${desc}"
    containers:
TOPO
    case "$sub" in
        core) cat <<'TOPO'
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
TOPO
        ;;
        ui) cat <<'TOPO'
      - infrabox-web
      - infrabox-backend
    ports_exposed:
      - { host: 80,  container: 80,  service: "web", proto: "http"  }
      - { host: 443, container: 443, service: "web", proto: "https" }
TOPO
        ;;
        arch) cat <<'TOPO'
      - infrabox-arch
      - infrabox-arch-backend
      - infrabox-arch-ui
    ports_exposed: []
TOPO
        ;;
        adm) cat <<'TOPO'
      - infrabox-adm
    ports_exposed: []
TOPO
        ;;
    esac
    # core отримує env (JWT/DROP_ID) — з розкриттям змінних
    if [ "$sub" = "core" ]; then
        cat <<TOPO
    env:
      JWT_SECRET:       "${JWT_SECRET}"
      JWT_EXPIRE_HOURS: "${JWT_EXPIRE}"
      DROP_ID:          "${DROP_ID}"
TOPO
    fi
    } >> "$OUT"
}

emit_sub core "$PLACE_core" "core" "Redis, MQTT-брокери, обробка точок, авторизація, симулятор, selfdiagnostic"
emit_sub arch "$PLACE_arch" "arch" "Архіватор: запис історії, backend та UI для трендів"
emit_sub ui   "$PLACE_ui"   "ui"   "Frontend (nginx+HTTPS), backend API, WebSocket"
emit_sub adm  "$PLACE_adm"  "adm"  "Адміністративний сервіс: контейнери, хост, конфігурація"

# ── docker network + порядок ──────────────────────────────────────────────────
{
cat <<'TOPO'

docker_network:
  name:   "infrabox-net"
  driver: "bridge"

deploy_order:
TOPO
echo "  - core"
[ -n "$PLACE_arch" ] && echo "  - arch"
[ -n "$PLACE_ui" ]   && echo "  - ui"
[ -n "$PLACE_adm" ]  && echo "  - adm"

echo ""
echo "undeploy_order:"
[ -n "$PLACE_adm" ]  && echo "  - adm"
[ -n "$PLACE_ui" ]   && echo "  - ui"
[ -n "$PLACE_arch" ] && echo "  - arch"
echo "  - core"

# ── data ──────────────────────────────────────────────────────────────────────
cat <<'TOPO'

data:
  - type:        "docker_volume"
    name:        "core_redis-data"
    description: "Redis дані (points, стан системи)"
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

if [ -n "$PLACE_arch" ]; then
cat <<'TOPO'

  - type:        "bind"
    path:        "arch/archivator/data"
    description: "Архів значень параметрів"
    critical:    true
TOPO
fi

if [ -n "$PLACE_ui" ]; then
cat <<'TOPO'

  - type:        "bind"
    path:        "ui/data"
    description: "Конфігурація UI: project.json, screens/*.json"
    critical:    true
TOPO
fi

if [ -n "$PLACE_adm" ]; then
cat <<'TOPO'

  - type:        "bind"
    path:        "adm/data"
    description: "Дані адмін-сервісу"
    critical:    false
TOPO
fi

cat <<'TOPO'

  - type:        "bind"
    path:        "log"
    description: "Спільні логи всіх підсистем"
    critical:    false
TOPO

# ── SSL ──────────────────────────────────────────────────────────────────────
if [ "$SSL_MODE" != "skip" ]; then
cat <<TOPO

ssl:
  mode:      "${SSL_MODE}"
  hostname:  "${SSL_HOST}"
  cert:      "ui/frontend/ssl/infrabox.crt"
  key:       "ui/frontend/ssl/infrabox.key"
TOPO
fi

echo ""
} >> "$OUT"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  topology.yml записано ✓${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
echo "  Файл: $(pwd)/${OUT}"
echo ""
echo "Наступні кроки:"
echo "  1. На КОЖНОМУ вузлі: bash host-prep.sh"
echo "  2. bash deploy.sh"
echo ""
warn "JWT_SECRET згенеровано — додайте його в env core при потребі: ${DEFAULT_JWT:0:16}…"
