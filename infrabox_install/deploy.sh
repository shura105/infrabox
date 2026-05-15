#!/usr/bin/env bash
# deploy.sh — розгортання Infrabox на вузли з topology.yml
#
# Usage:
#   bash deploy.sh                     → всі підсистеми (з deploy_order)
#   bash deploy.sh core ui             → тільки вказані, в порядку topology
#   bash deploy.sh --dry-run           → показати план без дій
#   bash deploy.sh --topology path.yml → альтернативний topology файл
#
# Що робить:
#   1. Парсить topology.yml
#   2. Перевіряє SSH-з'єднання з вузлом
#   3. git pull origin <branch>
#   4. docker compose up -d --remove-orphans (по підсистемах у deploy_order)
#   5. Виводить статус контейнерів

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗ ПОМИЛКА:${N} $*" >&2; exit 1; }
step() { echo -e "\n${C}── $* ──${N}"; }

# ── Аргументи ─────────────────────────────────────────────────────────────────
TOPOLOGY="${TOPOLOGY_FILE:-topology.yml}"
DRY_RUN=0
FILTER=""           # пробіл-розділені назви підсистем, або "" = всі

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)           DRY_RUN=1;            shift   ;;
        --topology)          TOPOLOGY="$2";         shift 2 ;;
        --*) fail "Невідомий аргумент: $1" ;;
        *)   FILTER="${FILTER:+$FILTER }$1"; shift ;;
    esac
done

[ -f "$TOPOLOGY" ] || fail "Не знайдено: $TOPOLOGY\nЗапустіть спочатку: bash wizard.sh"
command -v python3 &>/dev/null || fail "python3 не знайдено"

# ── Парсинг topology.yml ──────────────────────────────────────────────────────
# Вбудований Python3 парсер — без зовнішніх залежностей (PyYAML не потрібен).
# Виводить shell-змінні через eval.

_parse() {
python3 - "$TOPOLOGY" <<'PY'
import sys, json, re

with open(sys.argv[1]) as f:
    raw = f.read()

# Видалити коментарі та порожні рядки
lines = []
for line in raw.split('\n'):
    stripped = line.rstrip()
    # Зберігаємо порожні рядки та рядки з коментарями (потрібні для контексту відступів)
    lines.append(stripped)

def indent(line):
    return len(line) - len(line.lstrip(' '))

def unquote(s):
    s = s.strip()
    # Quoted string — extract only content inside quotes (ignore trailing comments)
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 0 else s[1:]
    if s.startswith("'"):
        end = s.find("'", 1)
        return s[1:end] if end > 0 else s[1:]
    # Unquoted — strip inline YAML comment
    if '  #' in s:
        s = s[:s.index('  #')].strip()
    elif ' #' in s:
        s = s[:s.index(' #')].strip()
    return s

data = {
    'system':       {},
    'nodes':        {},
    'subsystems':   {},
    'deploy_order': [],
}

ctx0 = None   # top-level section
ctx1 = None   # 2nd-level key (node alias or subsystem name)

for line in lines:
    content = line.rstrip()
    if not content or content.lstrip().startswith('#'):
        continue

    lvl = indent(content)
    text = content.strip()

    if lvl == 0:
        key = text.rstrip(':')
        ctx0 = key
        ctx1 = None
        continue

    if lvl == 2:
        if text.startswith('- '):
            val = text[2:].strip()
            if ctx0 == 'deploy_order':
                data['deploy_order'].append(val)
        elif ':' in text:
            key, sep, val = text.partition(':')
            key = key.strip()
            val = unquote(val)
            if ctx0 == 'system':
                data['system'][key] = val
            elif ctx0 in ('nodes', 'subsystems'):
                ctx1 = key
                if ctx0 == 'nodes':
                    data['nodes'][key] = {}
                else:
                    data['subsystems'][key] = {}
        continue

    if lvl == 4 and ':' in text:
        key, sep, val = text.partition(':')
        key = key.strip()
        val = unquote(val)
        if ctx0 == 'nodes' and ctx1:
            data['nodes'][ctx1][key] = val
        elif ctx0 == 'subsystems' and ctx1:
            data['subsystems'][ctx1][key] = val

# ── Виводимо shell-змінні ─────────────────────────────────────────────────────

sys_branch   = data['system'].get('branch', 'main')
sys_repo     = data['system'].get('repo', '')

if not data['nodes']:
    print("echo 'ERROR: nodes not found in topology.yml' >&2; exit 1")
    sys.exit(0)

node_alias   = list(data['nodes'].keys())[0]
node         = data['nodes'][node_alias]

node_host    = node.get('host', '')
node_user    = node.get('user', '')
node_key     = node.get('ssh_key', '~/.ssh/id_ed25519')
node_dir     = node.get('deploy_dir', '')

if not all([node_host, node_user, node_dir]):
    print("echo 'ERROR: incomplete node config in topology.yml' >&2; exit 1")
    sys.exit(0)

print(f'NODE_ALIAS={json.dumps(node_alias)}')
print(f'NODE_HOST={json.dumps(node_host)}')
print(f'NODE_USER={json.dumps(node_user)}')
print(f'NODE_KEY={json.dumps(node_key)}')
print(f'NODE_DEPLOY_DIR={json.dumps(node_dir)}')
print(f'SYS_BRANCH={json.dumps(sys_branch)}')
print(f'SYS_REPO={json.dumps(sys_repo)}')
print(f'DEPLOY_ORDER={json.dumps(" ".join(data["deploy_order"]))}')

# Workdir для кожної підсистеми як окремі змінні
for name, sub in data['subsystems'].items():
    varname = 'SUB_' + name.upper().replace('-','_') + '_WORKDIR'
    workdir = sub.get('workdir', name)
    print(f'{varname}={json.dumps(workdir)}')

PY
}

TOPO_VARS=$(_parse)
[ -z "$TOPO_VARS" ] && fail "Не вдалося розпарсити $TOPOLOGY"
eval "$TOPO_VARS"

# ── Формуємо список підсистем для цього запуску ───────────────────────────────
if [ -n "$FILTER" ]; then
    # Відфільтрувати за FILTER, зберігаючи порядок із DEPLOY_ORDER
    ACTIVE_SUBS=""
    for s in $DEPLOY_ORDER; do
        for f in $FILTER; do
            if [ "$s" = "$f" ]; then
                ACTIVE_SUBS="${ACTIVE_SUBS:+$ACTIVE_SUBS }$s"
                break
            fi
        done
    done
    [ -z "$ACTIVE_SUBS" ] && fail "Жодна підсистема не відповідає фільтру: $FILTER"
else
    ACTIVE_SUBS="$DEPLOY_ORDER"
fi

# ── SSH-хелпери ───────────────────────────────────────────────────────────────
KEY_PATH="${NODE_KEY/#\~/$HOME}"
_ssh_args() {
    if [ -f "$KEY_PATH" ]; then
        echo "-i $KEY_PATH -o StrictHostKeyChecking=accept-new"
    else
        echo "-o StrictHostKeyChecking=accept-new"
    fi
}
_ssh()   { ssh $(_ssh_args) "${NODE_USER}@${NODE_HOST}" "$@"; }
_ssh_q() { _ssh "$@" 2>/dev/null || true; }

# ── Заголовок ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${C}║               Infrabox  Deploy                       ║${N}"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"
echo ""
printf "  Вузол:     ${G}%s${N}  (%s)\n" "$NODE_ALIAS" "$NODE_HOST"
printf "  Deploy:    %s\n"   "$NODE_DEPLOY_DIR"
printf "  Branch:    %s\n"   "$SYS_BRANCH"
printf "  Порядок:   %s\n"   "$ACTIVE_SUBS"
[ "$DRY_RUN" = "1" ] && warn "DRY-RUN — жодних дій"
echo ""

# ── Крок 1: Перевірка SSH ─────────────────────────────────────────────────────
step "SSH"

if [ "$DRY_RUN" = "1" ]; then
    info "[dry] ssh -i $KEY_PATH ${NODE_USER}@${NODE_HOST}"
elif _ssh_q "exit 0"; then
    ok "SSH OK: ${NODE_USER}@${NODE_HOST}"
else
    fail "SSH недоступний: ${NODE_USER}@${NODE_HOST}  (ключ: $KEY_PATH)"
fi

# ── Крок 2: Git pull ──────────────────────────────────────────────────────────
step "Git pull → origin/${SYS_BRANCH}"

if [ "$DRY_RUN" = "1" ]; then
    info "[dry] cd ${NODE_DEPLOY_DIR} && git pull origin ${SYS_BRANCH}"
else
    GIT_OUT=$(_ssh "cd '${NODE_DEPLOY_DIR}' && git pull origin '${SYS_BRANCH}' 2>&1")
    if echo "$GIT_OUT" | grep -q "Already up to date"; then
        ok "Already up to date"
    else
        echo "$GIT_OUT" | head -10 | sed 's/^/    /'
        ok "Git pull виконано"
    fi
fi

# ── Крок 3: Docker compose up ─────────────────────────────────────────────────
step "Docker compose up -d"

for SUB in $ACTIVE_SUBS; do
    # Отримати workdir через indirect variable
    WD_VAR="SUB_$(echo "$SUB" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_WORKDIR"
    WORKDIR="${!WD_VAR:-$SUB}"
    COMPOSE_DIR="${NODE_DEPLOY_DIR}/${WORKDIR}"

    printf "\n  ${B}[%s]${N}  %s\n" "$SUB" "$COMPOSE_DIR"

    if [ "$DRY_RUN" = "1" ]; then
        info "[dry] cd ${COMPOSE_DIR} && docker compose up -d --remove-orphans"
        continue
    fi

    # Перевірити що директорія існує
    if ! _ssh_q "test -d '${COMPOSE_DIR}' && test -f '${COMPOSE_DIR}/docker-compose.yml' -o -f '${COMPOSE_DIR}/compose.yml'"; then
        warn "Пропускаємо ${SUB}: docker-compose.yml не знайдено у ${COMPOSE_DIR}"
        continue
    fi

    COMPOSE_OUT=$(_ssh "cd '${COMPOSE_DIR}' && docker compose up -d --remove-orphans 2>&1") || {
        echo "$COMPOSE_OUT" | tail -20 | sed 's/^/    /'
        fail "docker compose up -d failed: ${SUB}"
    }

    if echo "$COMPOSE_OUT" | grep -qi "error\b"; then
        echo "$COMPOSE_OUT" | grep -i "error" | sed 's/^/    /'
        warn "${SUB}: compose завершився з помилками (перевірте вище)"
    else
        ok "${SUB}: up ✓"
    fi
done

# ── Крок 4: Статус контейнерів ────────────────────────────────────────────────
step "Статус"

if [ "$DRY_RUN" = "0" ]; then
    STATUS=$(_ssh_q "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -i infrabox" || true)
    if [ -n "$STATUS" ]; then
        echo ""
        echo "$STATUS" | sed 's/^/  /'
        echo ""
        RUNNING=$(echo "$STATUS" | grep -c "Up " || true)
        ok "Запущено контейнерів: ${RUNNING}"
    else
        warn "Контейнери infrabox не знайдено (можливо ще стартують)"
    fi
fi

# ── Підсумок ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
if [ "$DRY_RUN" = "1" ]; then
    echo -e "${Y}  Dry-run завершено — жодних змін${N}"
else
    echo -e "${G}  Розгортання завершено ✓${N}"
fi
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
printf "  Вузол: %s  Branch: %s\n" "$NODE_ALIAS" "$SYS_BRANCH"
printf "  Підсистеми: %s\n" "$ACTIVE_SUBS"
echo ""
