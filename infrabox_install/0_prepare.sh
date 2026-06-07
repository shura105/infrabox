#!/usr/bin/env bash
# 0_prepare.sh — фундамент стеку Infrabox: формує ДЖЕРЕЛО ІСТИНИ (topology.yml)
#
#   bash 0_prepare.sh
#
# Це ПЕРШИЙ крок. Тут адмін приймає рішення (що відомо ДО оцінки хостів):
#   • система: repo, branch, назва, timezone, JWT
#   • вузли:   alias, host/IP, SSH-користувач, ключ, deploy_dir
#   • розподіл: яка підсистема (core/ui/arch/adm) на якому вузлі
#
# Результат — topology.yml, джерело істини для всього стеку:
#   1_probe (оцінка за роллю) → 2_host-prep → 3_deploy → 4_status / 5_uninstall
#
# Поля arch/os вузлів лишаються порожні — їх заповнить 1_probe (факти хоста).
# Requires: python3 (для генерації JWT; не обов'язково).

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

# ── Паралельні масиви вузлів (bash 3.2-safe) ──────────────────────────────────
ALIASES=(); HOSTS=(); USERS=(); KEYS=(); DIRS=()

add_node() {
    local n=$(( ${#ALIASES[@]} + 1 ))
    local a_alias a_host a_user a_key a_dir
    echo ""
    info "Вузол #${n}"
    ask "Псевдонім вузла (ключ у topology)"  "node${n}"            a_alias
    ask "Host або IP (краще .local-ім'я)"    ""                    a_host
    ask "SSH-користувач"                     "admin"               a_user
    ask "SSH-ключ (шлях на адмін-машині)"    "~/.ssh/id_ed25519"   a_key
    ask "Deploy directory"                   "/home/${a_user}/infrabox"  a_dir
    ALIASES+=("$a_alias"); HOSTS+=("$a_host"); USERS+=("$a_user")
    KEYS+=("$a_key"); DIRS+=("$a_dir")
    ok "Додано: ${a_alias} (${a_host})"
}

# ── Welcome ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${C}║      Infrabox — 0. Підготовка (джерело істини)       ║${N}"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"
echo ""
echo "  Крок 0 зі стеку. Тут формується topology.yml — джерело істини,"
echo "  яке читають усі наступні кроки (probe, host-prep, deploy)."

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
ask "JWT expire (годин)"  "24"  JWT_EXPIRE

# ── NODES ─────────────────────────────────────────────────────────────────────
hdr "Вузли"
echo "  Опишіть вузли (фізичні/віртуальні Linux-машини)."
add_node
while true; do
    echo ""
    ask_yn "Додати ще один вузол?" "n" MORE
    [ "$MORE" = "1" ] || break
    add_node
done
echo ""
ok "Вузлів: ${#ALIASES[@]}  (${ALIASES[*]})"

ask "DROP_ID (ідентифікатор у системі)"  "${ALIASES[0]}"  DROP_ID

# ── PLACEMENT ────────────────────────────────────────────────────────────────
hdr "Розподіл підсистем"
echo "  Оберіть вузол для кожної підсистеми (core обов'язковий)."

pick_node() {
    # pick_node <sub-опис> <allow_skip 0|1>  → друкує обраний alias (порожньо = пропустити)
    local desc="$1" allow_skip="$2" i choice
    {
        echo ""
        echo "  ${desc}:"
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
    printf "  Вузол: ${G}%s${N}  %s@%s  %s\n" \
        "${ALIASES[$i]}" "${USERS[$i]}" "${HOSTS[$i]}" "${DIRS[$i]}"
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

# ── GENERATE topology.yml ─────────────────────────────────────────────────────
GEN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

{
cat <<TOPO
# topology.yml — ДЖЕРЕЛО ІСТИНИ Infrabox
# Згенеровано 0_prepare.sh ${GEN_TS}
# arch/os вузлів заповнить 1_probe.sh. УВАГА: містить JWT_SECRET.

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
    arch:        ""          # заповнить 1_probe.sh
    os:          ""          # заповнить 1_probe.sh
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

# ── Done + probe-команди для кожного вузла ────────────────────────────────────
# роль вузла = підсистеми, призначені на нього
node_roles() {
    local alias="$1" roles=""
    [ "$PLACE_core" = "$alias" ] && roles="${roles:+$roles,}core"
    [ "$PLACE_arch" = "$alias" ] && roles="${roles:+$roles,}arch"
    [ "$PLACE_ui"   = "$alias" ] && roles="${roles:+$roles,}ui"
    [ "$PLACE_adm"  = "$alias" ] && roles="${roles:+$roles,}adm"
    echo "$roles"
}

echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  topology.yml створено ✓  (джерело істини)${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
echo "  Файл: $(pwd)/${OUT}"
echo ""
echo -e "${C}Наступний крок — 1. Оцінка хостів (probe):${N}"
echo "  Скопіюйте 1_probe.sh на кожен вузол і запустіть з його роллю:"
echo ""
for i in "${!ALIASES[@]}"; do
    ROLES=$(node_roles "${ALIASES[$i]}")
    [ -z "$ROLES" ] && continue
    printf "  ${D}# %s${N}\n" "${ALIASES[$i]}"
    printf "  ssh %s@%s 'bash -s -- --role %s' < 1_probe.sh\n\n" \
        "${USERS[$i]}" "${HOSTS[$i]}" "$ROLES"
done
echo "Далі: 2_host-prep.sh (на кожному вузлі) → 3_deploy.sh"
