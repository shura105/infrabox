#!/usr/bin/env bash
# status.sh — стан системи Infrabox
#
# Usage:
#   bash status.sh                  → повний огляд
#   bash status.sh --topology file  → альтернативний topology файл
#   bash status.sh --no-ports       → без перевірки портів (швидше)
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

# ── Парсинг topology.yml ──────────────────────────────────────────────────────
_parse() {
python3 - "$TOPOLOGY" <<'PY'
import sys, json, re

with open(sys.argv[1]) as f:
    raw = f.read()

def unquote(s):
    s = s.strip()
    if s.startswith('"'):
        end = s.find('"', 1); return s[1:end] if end > 0 else s[1:]
    if s.startswith("'"):
        end = s.find("'", 1); return s[1:end] if end > 0 else s[1:]
    if '  #' in s: s = s[:s.index('  #')].strip()
    elif ' #' in s: s = s[:s.index(' #')].strip()
    return s

def indent(line): return len(line) - len(line.lstrip(' '))

data = {'system': {}, 'nodes': {}, 'subsystems': {}, 'deploy_order': []}
ctx0 = None; ctx1 = None

for line in raw.split('\n'):
    s = line.rstrip()
    if not s or s.lstrip().startswith('#'): continue
    lvl = indent(s); text = s.strip()
    if lvl == 0:
        ctx0 = text.rstrip(':').strip(); ctx1 = None; continue
    if lvl == 2:
        if text.startswith('- ') and ctx0 == 'deploy_order':
            data['deploy_order'].append(text[2:].strip().strip('"'))
        elif ':' in text and not text.startswith('- '):
            k, _, v = text.partition(':'); k = k.strip()
            if ctx0 == 'system': data['system'][k] = unquote(v)
            elif ctx0 in ('nodes', 'subsystems'):
                ctx1 = k; data[ctx0][k] = {}
    elif lvl == 4 and ':' in text:
        k, _, v = text.partition(':'); k = k.strip(); v = unquote(v)
        if ctx0 == 'nodes' and ctx1: data['nodes'][ctx1][k] = v
        elif ctx0 == 'subsystems' and ctx1: data['subsystems'][ctx1][k] = v

if not data['nodes']:
    print("echo 'ERROR: nodes not found' >&2; exit 1"); sys.exit(0)

alias = list(data['nodes'].keys())[0]
node  = data['nodes'][alias]

def q(v): return json.dumps(str(v))

print(f'NODE_ALIAS={q(alias)}')
print(f'NODE_HOST={q(node.get("host",""))}')
print(f'NODE_USER={q(node.get("user",""))}')
print(f'NODE_KEY={q(node.get("ssh_key","~/.ssh/id_ed25519"))}')
print(f'NODE_DEPLOY_DIR={q(node.get("deploy_dir",""))}')
print(f'NODE_ARCH={q(node.get("arch",""))}')
print(f'SYS_BRANCH={q(data["system"].get("branch","main"))}')
print(f'SYS_NAME={q(data["system"].get("name","Infrabox"))}')
print(f'DEPLOY_ORDER={q(" ".join(data["deploy_order"]))}')

# Очікувані контейнери
expected = []
for sub in data['subsystems'].values():
    pass  # containers are nested list items — extract via regex below

# Витягнути контейнери регексом (надійніше для списків)
containers = re.findall(r'^\s+- (infrabox-\S+)', raw, re.MULTILINE)
print(f'EXPECTED_CONTAINERS={q(" ".join(containers))}')

# Зовнішні порти — з рядків { host: N, ... }
ports = re.findall(r'host:\s*(\d+)', raw)
ports = list(dict.fromkeys(ports))  # uniq, preserve order
print(f'EXPOSED_PORTS={q(" ".join(ports))}')

PY
}

eval "$(_parse)"

# ── SSH ───────────────────────────────────────────────────────────────────────
KEY_PATH="${NODE_KEY/#\~/$HOME}"
_ssh_opt() {
    [ -f "$KEY_PATH" ] && echo "-i $KEY_PATH -o StrictHostKeyChecking=accept-new" \
                       || echo "-o StrictHostKeyChecking=accept-new"
}
_ssh() { ssh $(_ssh_opt) "${NODE_USER}@${NODE_HOST}" "$@"; }

# ── Збір даних одним SSH-дзвінком ─────────────────────────────────────────────
DEPLOY_DIR="$NODE_DEPLOY_DIR"

# Передаємо DEPLOY_DIR як $1 до remote bash, використовуємо квотований heredoc
# (одинарні лапки у <<'REMOTE' вимикають локальне розкриття bash — awk $2 etc. цілі)
RAW=$(_ssh bash -s "${NODE_DEPLOY_DIR}" <<'REMOTE'
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

printf '=VOLUMES=\n'
docker volume ls --format '{{.Name}}' 2>/dev/null | grep -i infrabox || true

printf '=DOCKER_INFO=\n'
docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '?'
REMOTE
) 2>/dev/null || { echo -e "${R}✗ SSH недоступний: ${NODE_USER}@${NODE_HOST}${N}" >&2; exit 1; }

# ── Парсинг секцій ─────────────────────────────────────────────────────────────
_section() {
    # Витягнути вміст між =SECTION= і наступним =...=
    echo "$RAW" | awk "/^=$1=\$/{found=1; next} found && /^=[A-Z_]+=\$/{exit} found{print}"
}

SEC_CONTAINERS=$(_section CONTAINERS)
SEC_STOPPED=$(_section STOPPED)
SEC_MEMORY=$(_section MEMORY)
SEC_DISK=$(_section DISK)
SEC_LOAD=$(_section LOAD)
SEC_UPTIME=$(_section UPTIME)
SEC_GIT_BRANCH=$(_section GIT_BRANCH)
SEC_GIT_HEAD=$(_section GIT_HEAD)
SEC_GIT_DIRTY=$(_section GIT_DIRTY)
SEC_VOLUMES=$(_section VOLUMES)
SEC_DOCKER_VER=$(_section DOCKER_INFO)

# ── Заголовок ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
printf  "${C}║  %-52s║${N}\n" "${SYS_NAME}  —  Status"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"
echo ""
printf  "  Вузол:   ${G}%s${N}  (%s)  %s\n" "$NODE_ALIAS" "$NODE_HOST" "$NODE_ARCH"
printf  "  Docker:  %s\n" "${SEC_DOCKER_VER:-?}"
printf  "  Uptime:  %s\n" "${SEC_UPTIME:-?}"

# ── Git ───────────────────────────────────────────────────────────────────────
hdr "Git"

GIT_BRANCH="${SEC_GIT_BRANCH:-unknown}"
GIT_HASH=$(echo "${SEC_GIT_HEAD:-?}" | sed -n '1p')
GIT_MSG=$( echo "${SEC_GIT_HEAD:-?}" | sed -n '2p')
GIT_AGO=$( echo "${SEC_GIT_HEAD:-?}" | sed -n '3p')
GIT_DIRTY="${SEC_GIT_DIRTY:-0}"

if [ "$GIT_BRANCH" = "$SYS_BRANCH" ]; then
    printf "  Branch:  ${G}%s${N}" "$GIT_BRANCH"
else
    printf "  Branch:  ${Y}%s${N} ${D}(очікується: %s)${N}" "$GIT_BRANCH" "$SYS_BRANCH"
fi

if [ "${GIT_DIRTY:-0}" = "0" ]; then
    echo -e "  ${D}(clean)${N}"
else
    echo -e "  ${Y}(${GIT_DIRTY} змін)${N}"
fi

printf "  HEAD:    %s  %s  ${D}%s${N}\n" "$GIT_HASH" "$GIT_MSG" "$GIT_AGO"

# ── Контейнери ────────────────────────────────────────────────────────────────
hdr "Контейнери"

RUNNING_COUNT=0
TOTAL_EXPECTED=0

for C in $EXPECTED_CONTAINERS; do
    TOTAL_EXPECTED=$((TOTAL_EXPECTED + 1))
    # Шукаємо контейнер у запущених
    LINE=$(echo "$SEC_CONTAINERS" | grep "^${C}|||" || true)
    if [ -n "$LINE" ]; then
        # ||| separator → f1=name f2='' f3='' f4=status f5='' f6='' f7=ports
        STATUS=$(echo "$LINE" | cut -d'|' -f4)
        PORTS=$(echo "$LINE"  | cut -d'|' -f7)
        RUNNING_COUNT=$((RUNNING_COUNT + 1))
        # Визначити колір за станом
        if echo "$STATUS" | grep -qi "healthy"; then
            COLOR="$G"
        elif echo "$STATUS" | grep -qi "^Up"; then
            COLOR="$G"
        else
            COLOR="$Y"
        fi
        # Обрізати порти для компактності
        PORT_SHORT=$(echo "$PORTS" | sed 's/0\.0\.0\.0://g; s/:::://g' | cut -c1-35)
        printf "  ${COLOR}%-30s${N}  %-22s  ${D}%s${N}\n" "$C" "$STATUS" "$PORT_SHORT"
    else
        # Перевірити чи є у stopped
        STOPPED_LINE=$(echo "$SEC_STOPPED" | grep "^${C}|||" || true)
        if [ -n "$STOPPED_LINE" ]; then
            STOP_STATUS=$(echo "$STOPPED_LINE" | cut -d'|' -f4)
            printf "  ${R}%-30s${N}  ${R}%s${N}\n" "$C" "$STOP_STATUS"
        else
            printf "  ${R}%-30s${N}  ${R}not found${N}\n" "$C"
        fi
    fi
done

echo ""
if [ "$RUNNING_COUNT" = "$TOTAL_EXPECTED" ]; then
    ok "${RUNNING_COUNT}/${TOTAL_EXPECTED} running"
    STATUS_OK=1
else
    warn "${RUNNING_COUNT}/${TOTAL_EXPECTED} running"
    STATUS_OK=0
fi

# Контейнери що є але не в topology (case замість grep -w — надійніше з дефісами)
EXPECTED_PAD=" $EXPECTED_CONTAINERS "
EXTRA=""
while IFS= read -r line; do
    [ -z "$line" ] && continue
    CNAME=$(echo "$line" | cut -d'|' -f1)
    case "$EXPECTED_PAD" in
        *" $CNAME "*) ;;   # є в topology
        *) EXTRA="${EXTRA:+$EXTRA }$CNAME" ;;
    esac
done <<< "$SEC_CONTAINERS"
if [ -n "$EXTRA" ]; then
    warn "Зайві контейнери (не в topology): $EXTRA"
fi

# ── Docker Volumes ────────────────────────────────────────────────────────────
if [ -n "${SEC_VOLUMES:-}" ]; then
    hdr "Volumes"
    # <<< уникає subshell-у (на відміну від echo | while)
    while IFS= read -r vol; do
        [ -z "$vol" ] && continue
        printf "  ${D}%s${N}\n" "$vol"
    done <<< "$SEC_VOLUMES"
fi

# ── Ресурси ───────────────────────────────────────────────────────────────────
hdr "Ресурси"

# RAM
MEM_TOTAL=$(echo "${SEC_MEMORY:-0 0 0}" | awk '{print $1}')
MEM_USED=$(echo  "${SEC_MEMORY:-0 0 0}" | awk '{print $2}')
MEM_FREE=$(echo  "${SEC_MEMORY:-0 0 0}" | awk '{print $3}')
if [ "${MEM_TOTAL:-0}" -gt 0 ] 2>/dev/null; then
    MEM_PCT=$(( MEM_USED * 100 / MEM_TOTAL ))
    if [ "$MEM_PCT" -gt 85 ]; then MC="$R"
    elif [ "$MEM_PCT" -gt 65 ]; then MC="$Y"
    else MC="$G"; fi
    printf "  RAM:  %dMB total  ${MC}%dMB used (%d%%)${N}  %dMB free\n" \
        "$MEM_TOTAL" "$MEM_USED" "$MEM_PCT" "$MEM_FREE"
else
    printf "  RAM:  %s\n" "${SEC_MEMORY:-n/a}"
fi

# Disk
DISK_TOT=$(echo "${SEC_DISK:-0 0 0 0}" | awk '{printf "%.1f", $1/1024}')
DISK_USD=$(echo "${SEC_DISK:-0 0 0 0}" | awk '{printf "%.1f", $2/1024}')
DISK_FREE=$(echo "${SEC_DISK:-0 0 0 0}"| awk '{printf "%.1f", $3/1024}')
DISK_PCT=$(echo "${SEC_DISK:-0 0 0 0}" | awk '{print $4}')
if [ "${DISK_PCT:-0%}" != "0%" ]; then
    PCT_N=$(echo "$DISK_PCT" | tr -d '%')
    if [ "${PCT_N:-0}" -gt 85 ] 2>/dev/null; then DC="$R"
    elif [ "${PCT_N:-0}" -gt 65 ] 2>/dev/null; then DC="$Y"
    else DC="$G"; fi
    printf "  Disk: %.1fGB total  ${DC}%.1fGB used (%s)${N}  %.1fGB free\n" \
        "$DISK_TOT" "$DISK_USD" "$DISK_PCT" "$DISK_FREE"
else
    printf "  Disk: %s\n" "${SEC_DISK:-n/a}"
fi

# Load
LOAD=$(echo "${SEC_LOAD:-? ? ?}" | awk '{print $1,$2,$3}')
printf "  Load: %s\n" "$LOAD"

# ── Порти ────────────────────────────────────────────────────────────────────
if [ "$CHECK_PORTS" = "1" ] && [ -n "${EXPOSED_PORTS:-}" ]; then
    hdr "Порти (з адмін-машини)"

    _port_label() {
        case "$1" in
            80)   echo "HTTP"   ;; 443)  echo "HTTPS"  ;;
            1883) echo "MQTT"   ;; 1884) echo "MQTT-sim" ;;
            6379) echo "Redis"  ;; 8099) echo "backend" ;;
            *)    echo "port$1" ;;
        esac
    }

    for PORT in $EXPOSED_PORTS; do
        LABEL=$(_port_label "$PORT")
        if command -v nc &>/dev/null; then
            if nc -z -w2 "$NODE_HOST" "$PORT" 2>/dev/null; then
                printf "  ${G}✓${N}  %-6s  %s\n" "$PORT" "$LABEL"
            else
                printf "  ${R}✗${N}  %-6s  %s  ${D}(недоступний)${N}\n" "$PORT" "$LABEL"
                STATUS_OK=0
            fi
        else
            printf "  ${D}?${N}  %-6s  %s  ${D}(nc не знайдено)${N}\n" "$PORT" "$LABEL"
        fi
    done
fi

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

# Код виходу для автоматизації
[ "${STATUS_OK:-1}" = "1" ]
