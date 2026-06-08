#!/usr/bin/env bash
# 1_prepare.sh — формує ДЖЕРЕЛО ІСТИНИ (topology.yml) зі звітів 0_probe.
#
#   bash 1_prepare.sh [host-report.json ...]
#
# ДРУГИЙ крок. На вхід — host-report.json кожного вузла (з 0_probe). Скрипт:
#   • бере ФАКТИ зі звітів: hostname, IP/.local, arch, os
#   • бере РОЗПОДІЛ ролей зі звітів: requested_roles (що планували на вузол)
#   • питає РІШЕННЯ адміна: SSH-користувач/ключ, deploy_dir, repo/branch/JWT, SSL
#   → topology.yml — джерело істини для 2_host-prep → 3_deploy → 4_status / 5_uninstall
#
# Без аргументів шукає host-report*.json у поточній директорії.
# Requires: python3 або jq (читання звітів).

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

# ── Звіти на вхід ─────────────────────────────────────────────────────────────
REPORTS=()
if [ $# -gt 0 ]; then
    for a in "$@"; do [ -f "$a" ] || fail "Не знайдено: $a"; REPORTS+=("$a"); done
else
    for f in host-report*.json; do [ -f "$f" ] && REPORTS+=("$f"); done
fi
[ "${#REPORTS[@]}" -eq 0 ] && fail "Немає host-report.json.\nСпершу запустіть 0_probe.sh на вузлах і принесіть звіти."

command -v python3 &>/dev/null || command -v jq &>/dev/null || fail "Потрібен python3 або jq"
command -v jq &>/dev/null && HAS_JQ=1 || HAS_JQ=0

# ── JSON getter ───────────────────────────────────────────────────────────────
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
            n, r = k.split('[', 1); v = v[n][int(r.rstrip(']'))]
        else: v = v[k]
    print('' if v is None else v)
except Exception:
    print('')
PY
    fi
}
_get_roles() {
    local file="$1"
    if [ "$HAS_JQ" = "1" ]; then
        jq -r '(.requested_roles // []) | join(" ")' "$file" 2>/dev/null || true
    else
        python3 - "$file" 2>/dev/null <<'PY'
import sys, json
print(' '.join(json.load(open(sys.argv[1])).get('requested_roles', [])))
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
        printf "  (обов'язково) %s: " "$prompt"; read -r result || true
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
        aarch64|arm64) echo "arm64" ;;
        *)       echo "${1:-unknown}" ;;
    esac
}

# ── Паралельні масиви вузлів (bash 3.2-safe) ──────────────────────────────────
ALIASES=(); HOSTS=(); USERS=(); KEYS=(); DIRS=(); ARCHS=(); OSES=(); NROLES=()

add_node_from_report() {
    local rep="$1"
    local p_host p_mdns p_ip p_arch p_osid p_osver p_roles
    p_host=$(_get "$rep" '.network.hostname')
    p_mdns=$(_get "$rep" '.network.mdns_name')
    p_ip=$(_get   "$rep" '.network.primary_ip')
    p_arch=$(_map_arch "$(_get "$rep" '.hardware.arch')")
    p_osid=$(_get  "$rep" '.os.id')
    p_osver=$(_get "$rep" '.os.version')
    p_roles=$(_get_roles "$rep")

    local d_host="$p_mdns"
    [ -z "$d_host" ] && d_host="${p_ip:-${p_host}.local}"

    echo ""
    info "Звіт: ${rep}"
    printf "  ${G}%s${N}  %s  arch:%s  os:%s/%s  ролі:[%s]\n" \
        "${p_host:-?}" "${p_ip:-?}" "$p_arch" "${p_osid:-?}" "${p_osver:-?}" "${p_roles:-—}"

    local a_alias a_host a_user a_key a_dir
    ask "Псевдонім вузла"        "${p_host:-node}"  a_alias
    ask "Host (.local або IP)"   "$d_host"          a_host
    ask "SSH-користувач"         "admin"            a_user
    ask "SSH-ключ (на адмінці)"  "~/.ssh/id_ed25519" a_key
    ask "Deploy directory"       "/home/${a_user}/infrabox"  a_dir

    ALIASES+=("$a_alias"); HOSTS+=("$a_host"); USERS+=("$a_user")
    KEYS+=("$a_key"); DIRS+=("$a_dir"); ARCHS+=("$p_arch"); OSES+=("${p_osid}/${p_osver}")
    NROLES+=("$p_roles")
    ok "Вузол: ${a_alias} (${a_host})  ролі: ${p_roles:-—}"
}

# ── Welcome ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${C}║      Infrabox — 1. Підготовка (джерело істини)       ║${N}"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"
echo ""
echo "  Формуємо topology.yml зі звітів 0_probe (факти + наміри),"
echo "  доповнюючи рішеннями адміна (SSH, deploy_dir, repo/branch)."

# ── NODES (зі звітів) ─────────────────────────────────────────────────────────
hdr "Вузли (зі звітів 0_probe)"
for rep in "${REPORTS[@]}"; do
    add_node_from_report "$rep"
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

hdr "Безпека"
DEFAULT_JWT=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "CHANGE_ME_$(date +%s)")
printf "  ${B}JWT_SECRET${N} [авто: ${G}%.16s${N}…]: " "$DEFAULT_JWT"
read -r jwt_input || true
JWT_SECRET="${jwt_input:-$DEFAULT_JWT}"
ask "JWT expire (годин)"  "24"             JWT_EXPIRE
ask "DROP_ID"             "${ALIASES[0]}"  DROP_ID

# ── PLACEMENT (дефолти з requested_roles) ─────────────────────────────────────
hdr "Розподіл підсистем"
echo "  Запропоновано зі звітів (requested_roles). Enter — прийняти, або змінити."

# дефолтний розподіл з NROLES
def_core=""; def_ui=""; def_arch=""; def_adm=""
for i in "${!ALIASES[@]}"; do
    for r in ${NROLES[$i]}; do
        case "$r" in
            core) def_core="${ALIASES[$i]}" ;;
            ui)   def_ui="${ALIASES[$i]}"   ;;
            arch) def_arch="${ALIASES[$i]}" ;;
            adm)  def_adm="${ALIASES[$i]}"  ;;
        esac
    done
done

pick_node() {
    # pick_node <опис> <allow_skip 0|1> <default_alias>  → друкує обраний alias
    local desc="$1" allow_skip="$2" def="$3" i choice def_idx=""
    {
        echo ""
        echo "  ${desc}:"
        for i in "${!ALIASES[@]}"; do
            printf "    %d) %s (%s)\n" $((i+1)) "${ALIASES[$i]}" "${HOSTS[$i]}"
            [ "${ALIASES[$i]}" = "$def" ] && def_idx=$((i+1))
        done
        [ "$allow_skip" = "1" ] && echo "    0) не встановлювати"
    } >&2
    local prompt_def="${def_idx:-1}"; [ -z "$def" ] && [ "$allow_skip" = "1" ] && prompt_def="0"
    while true; do
        printf "  Вибір [%s]: " "$prompt_def" >&2
        read -r choice || true
        choice="${choice:-$prompt_def}"
        if [ "$choice" = "0" ] && [ "$allow_skip" = "1" ]; then echo ""; return; fi
        if [ "$choice" -ge 1 ] 2>/dev/null && [ "$choice" -le "${#ALIASES[@]}" ]; then
            echo "${ALIASES[$((choice-1))]}"; return
        fi
        echo "  Невірний вибір" >&2
    done
}

PLACE_core=$(pick_node "core (Redis, MQTT, auth, simulator, selfdiag)" 0 "$def_core")
PLACE_ui=$(pick_node   "ui (web + backend API)" 1 "$def_ui")
PLACE_arch=$(pick_node "arch (архіватор історії)" 1 "$def_arch")
PLACE_adm=$(pick_node  "adm (адмін-сервіс)" 1 "$def_adm")
[ -z "$PLACE_core" ] && fail "core обов'язковий"

# ── SSL ──────────────────────────────────────────────────────────────────────
hdr "SSL"
echo "  1) mkcert   2) self-signed   3) skip"
printf "  ${B}Вибір${N} [1]: "
read -r ssl_pick || true
case "${ssl_pick:-1}" in
    2) SSL_MODE="selfsigned" ;; 3) SSL_MODE="skip" ;; *) SSL_MODE="mkcert" ;;
esac
SSL_HOST=""
if [ "$SSL_MODE" != "skip" ]; then
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
    printf "  Вузол: ${G}%s${N}  %s@%s  %s  [%s %s]\n" \
        "${ALIASES[$i]}" "${USERS[$i]}" "${HOSTS[$i]}" "${DIRS[$i]}" "${ARCHS[$i]}" "${OSES[$i]}"
done
echo ""
printf "  core → ${G}%s${N}   ui → %s   arch → %s   adm → %s\n" \
    "$PLACE_core" "${PLACE_ui:-—}" "${PLACE_arch:-—}" "${PLACE_adm:-—}"
printf "  repo/branch: %s @ %s   SSL: %s%s\n" "$SYS_REPO" "$SYS_BRANCH" "$SSL_MODE" \
    "$([ -n "$SSL_HOST" ] && printf ' → %s' "$SSL_HOST")"
echo ""

[ -f "$OUT" ] && warn "$OUT вже існує — буде перезаписано"
ask_yn "Записати topology.yml?" "y" DO_WRITE
[ "$DO_WRITE" = "1" ] || { warn "Скасовано."; exit 0; }

# ── GENERATE topology.yml ─────────────────────────────────────────────────────
GEN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
cat <<TOPO
# topology.yml — ДЖЕРЕЛО ІСТИНИ Infrabox
# Згенеровано 1_prepare.sh ${GEN_TS} зі звітів 0_probe. УВАГА: містить JWT_SECRET.

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
[ -n "$PLACE_arch" ] && cat <<'TOPO'

  - type:        "bind"
    path:        "arch/archivator/data"
    description: "Архів значень параметрів"
    critical:    true
TOPO
[ -n "$PLACE_ui" ] && cat <<'TOPO'

  - type:        "bind"
    path:        "ui/data"
    description: "Конфігурація UI: project.json, screens/*.json"
    critical:    true
TOPO
[ -n "$PLACE_adm" ] && cat <<'TOPO'

  - type:        "bind"
    path:        "adm/data"
    description: "Дані адмін-сервісу"
    critical:    false
TOPO
cat <<'TOPO'

  - type:        "bind"
    path:        "log"
    description: "Спільні логи всіх підсистем"
    critical:    false
TOPO
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
echo -e "${G}  topology.yml створено ✓  (джерело істини)${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
echo "  Файл: $(pwd)/${OUT}"
echo ""
echo "Наступні кроки:"
echo "  2. bash 2_host-prep.sh   (на кожному вузлі)"
echo "  3. bash 3_deploy.sh      (з admin-машини)"
