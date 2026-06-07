#!/usr/bin/env bash
# 4_status.sh — стан системи Infrabox (мультихост)
#
# Usage:
#   bash 4_status.sh                  → повний огляд усіх вузлів
#   bash 4_status.sh --topology file  → альтернативний topology файл
#   bash 4_status.sh --no-ports       → без перевірки портів (швидше)
#
# Вихід: 0 = все OK, 1 = є проблеми (для моніторингу/CI)

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'
D='\033[2m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗${N} $*"; }
hdr()  { echo -e "\n${C}── $* ──${N}"; }

# ── Шлях до скрипта ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Аргументи ─────────────────────────────────────────────────────────────────
TOPOLOGY="${TOPOLOGY_FILE:-topology.yml}"
CHECK_PORTS=1

while [ $# -gt 0 ]; do
    case "$1" in
        --topology)  TOPOLOGY="$2"; shift 2 ;;
        --no-ports)  CHECK_PORTS=0; shift   ;;
        *) echo "Невідомий аргумент: $1" >&2; exit 1 ;;
    esac
done

[ -f "$TOPOLOGY" ] || { echo "Не знайдено: $TOPOLOGY" >&2; exit 1; }
command -v python3 &>/dev/null || { echo "python3 не знайдено" >&2; exit 1; }
[ -f "$SCRIPT_DIR/_topo.py" ] || { echo "Не знайдено: $SCRIPT_DIR/_topo.py" >&2; exit 1; }

TOPO() { python3 "$SCRIPT_DIR/_topo.py" "$TOPOLOGY" "$@"; }

eval "$(TOPO system)"

# ── SSH (працює з поточним вузлом) ────────────────────────────────────────────
use_node() {
    eval "$(TOPO node "$1")"
    CUR_KEY="${NODE_KEY/#\~/$HOME}"
}
_ssh_opt() {
    [ -f "$CUR_KEY" ] && echo "-i $CUR_KEY -o StrictHostKeyChecking=accept-new" \
                      || echo "-o StrictHostKeyChecking=accept-new"
}
_ssh() { ssh $(_ssh_opt) "${NODE_USER}@${NODE_HOST}" "$@"; }

# ── Remote-збір одним SSH-дзвінком ────────────────────────────────────────────
collect() {
    _ssh bash -s "${NODE_DEPLOY_DIR}" <<'REMOTE'
#!/bin/sh
DEPLOY_DIR="$1"

printf '=CONTAINERS=\n'
docker ps --format '{{.Names}}|||{{.Status}}|||{{.Ports}}' 2>/dev/null \
    | grep -i infrabox | sort || true

printf '=STOPPED=\n'
docker ps -a --filter status=exited --filter status=created \
    --format '{{.Names}}|||{{.Status}}' 2>/dev/null \
    | grep -i infrabox | sort || true

printf '=MEMORY=\n'
free -m 2>/dev/null | awk '/^Mem/{print $2,$3,$4}' || echo '0 0 0'

printf '=DISK=\n'
df -BM "$DEPLOY_DIR" 2>/dev/null \
    | awk 'NR==2{gsub("M","",$2); gsub("M","",$3); gsub("M","",$4); print $2,$3,$4,$5}' \
    || echo '0 0 0 0'

printf '=LOAD=\n'
cat /proc/loadavg 2>/dev/null | awk '{print $1,$2,$3}' || echo '? ? ?'

printf '=UPTIME=\n'
uptime -p 2>/dev/null || uptime 2>/dev/null || echo 'unknown'

printf '=GIT_BRANCH=\n'
git -C "$DEPLOY_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown'

printf '=GIT_HEAD=\n'
git -C "$DEPLOY_DIR" log -1 --format='%h%n%s%n%ar' 2>/dev/null || printf '?\n?\n?'

printf '=GIT_DIRTY=\n'
COUNT=$(git -C "$DEPLOY_DIR" status --short 2>/dev/null | wc -l | tr -d ' ')
echo "${COUNT:-0}"

printf '=DOCKER_INFO=\n'
docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '?'
REMOTE
}

_section() {
    # $1=section name, $2=RAW text
    echo "$2" | awk "/^=$1=\$/{found=1; next} found && /^=[A-Z_]+=\$/{exit} found{print}"
}

# ── Заголовок ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
printf  "${C}║  %-52s║${N}\n" "${SYS_NAME}  —  Status"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"

STATUS_OK=1
NODES="$(TOPO nodes)"

# ── Цикл по вузлах ────────────────────────────────────────────────────────────
for NODE in $NODES; do
    # підсистеми, призначені на цей вузол
    SUBS_HERE="$(TOPO subs-on "$NODE" | tr '\n' ' ')"
    [ -z "$(echo "$SUBS_HERE" | tr -d ' ')" ] && continue   # вузол без підсистем

    use_node "$NODE"

    echo ""
    echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
    printf  "  ${G}%s${N}  (%s)  %s\n" "$NODE" "$NODE_HOST" "$NODE_ARCH"

    # — збір —
    if ! RAW=$(collect 2>/dev/null); then
        fail "  SSH недоступний: ${NODE_USER}@${NODE_HOST}"
        STATUS_OK=0
        continue
    fi

    SEC_CONTAINERS=$(_section CONTAINERS "$RAW")
    SEC_STOPPED=$(_section STOPPED "$RAW")
    SEC_MEMORY=$(_section MEMORY "$RAW")
    SEC_DISK=$(_section DISK "$RAW")
    SEC_LOAD=$(_section LOAD "$RAW")
    SEC_UPTIME=$(_section UPTIME "$RAW")
    SEC_GIT_BRANCH=$(_section GIT_BRANCH "$RAW")
    SEC_GIT_HEAD=$(_section GIT_HEAD "$RAW")
    SEC_GIT_DIRTY=$(_section GIT_DIRTY "$RAW")
    SEC_DOCKER_VER=$(_section DOCKER_INFO "$RAW")

    printf "  Docker:  %s   Uptime: %s\n" "${SEC_DOCKER_VER:-?}" "${SEC_UPTIME:-?}"

    # — Git —
    GIT_BRANCH="${SEC_GIT_BRANCH:-unknown}"
    GIT_HASH=$(echo "${SEC_GIT_HEAD:-?}" | sed -n '1p')
    GIT_MSG=$( echo "${SEC_GIT_HEAD:-?}" | sed -n '2p')
    GIT_AGO=$( echo "${SEC_GIT_HEAD:-?}" | sed -n '3p')
    GIT_DIRTY="${SEC_GIT_DIRTY:-0}"

    if [ "$GIT_BRANCH" = "$SYS_BRANCH" ]; then
        printf "  Git:     ${G}%s${N}" "$GIT_BRANCH"
    else
        printf "  Git:     ${Y}%s${N} ${D}(очікується: %s)${N}" "$GIT_BRANCH" "$SYS_BRANCH"
    fi
    if [ "${GIT_DIRTY:-0}" = "0" ]; then
        echo -e "  ${D}(clean)${N}  ${D}${GIT_HASH} ${GIT_MSG} ${GIT_AGO}${N}"
    else
        echo -e "  ${Y}(${GIT_DIRTY} змін)${N}  ${D}${GIT_HASH} ${GIT_MSG} ${GIT_AGO}${N}"
    fi

    # — Контейнери (очікувані на цьому вузлі) —
    EXPECTED=""
    for s in $SUBS_HERE; do
        for c in $(TOPO sub-containers "$s"); do
            EXPECTED="${EXPECTED:+$EXPECTED }$c"
        done
    done

    RUNNING_COUNT=0
    TOTAL_EXPECTED=0
    echo "  Контейнери:"
    for CT in $EXPECTED; do
        TOTAL_EXPECTED=$((TOTAL_EXPECTED + 1))
        LINE=$(echo "$SEC_CONTAINERS" | grep "^${CT}|||" || true)
        if [ -n "$LINE" ]; then
            CSTATUS=$(echo "$LINE" | cut -d'|' -f4)
            PORTS=$(echo "$LINE"  | cut -d'|' -f7)
            RUNNING_COUNT=$((RUNNING_COUNT + 1))
            if echo "$CSTATUS" | grep -qiE "healthy|^Up"; then COLOR="$G"; else COLOR="$Y"; fi
            PORT_SHORT=$(echo "$PORTS" | sed 's/0\.0\.0\.0://g; s/:::://g' | cut -c1-30)
            printf "    ${COLOR}%-28s${N}  %-20s  ${D}%s${N}\n" "$CT" "$CSTATUS" "$PORT_SHORT"
        else
            STOPPED_LINE=$(echo "$SEC_STOPPED" | grep "^${CT}|||" || true)
            if [ -n "$STOPPED_LINE" ]; then
                STOP_STATUS=$(echo "$STOPPED_LINE" | cut -d'|' -f4)
                printf "    ${R}%-28s${N}  ${R}%s${N}\n" "$CT" "$STOP_STATUS"
            else
                printf "    ${R}%-28s${N}  ${R}not found${N}\n" "$CT"
            fi
            STATUS_OK=0
        fi
    done
    if [ "$RUNNING_COUNT" = "$TOTAL_EXPECTED" ]; then
        ok "  ${RUNNING_COUNT}/${TOTAL_EXPECTED} running"
    else
        warn "  ${RUNNING_COUNT}/${TOTAL_EXPECTED} running"
        STATUS_OK=0
    fi

    # — Ресурси —
    MEM_TOTAL=$(echo "${SEC_MEMORY:-0 0 0}" | awk '{print $1}')
    MEM_USED=$(echo  "${SEC_MEMORY:-0 0 0}" | awk '{print $2}')
    if [ "${MEM_TOTAL:-0}" -gt 0 ] 2>/dev/null; then
        MEM_PCT=$(( MEM_USED * 100 / MEM_TOTAL ))
        if [ "$MEM_PCT" -gt 85 ]; then MC="$R"; elif [ "$MEM_PCT" -gt 65 ]; then MC="$Y"; else MC="$G"; fi
        printf "  RAM: %dMB total  ${MC}%dMB (%d%%)${N}" "$MEM_TOTAL" "$MEM_USED" "$MEM_PCT"
    fi
    DISK_PCT=$(echo "${SEC_DISK:-0 0 0 0}" | awk '{print $4}')
    LOAD=$(echo "${SEC_LOAD:-? ? ?}" | awk '{print $1,$2,$3}')
    printf "   Disk: %s   Load: %s\n" "${DISK_PCT:-?}" "$LOAD"

    # — Порти цього вузла —
    if [ "$CHECK_PORTS" = "1" ]; then
        PORTS_HERE="$(TOPO ports "$NODE" | tr '\n' ' ')"
        if [ -n "$(echo "$PORTS_HERE" | tr -d ' ')" ]; then
            printf "  Порти:"
            for PORT in $PORTS_HERE; do
                if command -v nc &>/dev/null; then
                    if nc -z -w2 "$NODE_HOST" "$PORT" 2>/dev/null; then
                        printf "  ${G}%s✓${N}" "$PORT"
                    else
                        printf "  ${R}%s✗${N}" "$PORT"
                        STATUS_OK=0
                    fi
                else
                    printf "  ${D}%s?${N}" "$PORT"
                fi
            done
            echo ""
        fi
    fi
done

# ── Підсумок ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}──────────────────────────────────────────${N}"
if [ "${STATUS_OK:-1}" = "1" ]; then
    echo -e "${G}  Система в нормі ✓${N}"
else
    echo -e "${Y}  Є проблеми — перевірте вище${N}"
fi
echo -e "${C}──────────────────────────────────────────${N}"
echo ""

[ "${STATUS_OK:-1}" = "1" ]
