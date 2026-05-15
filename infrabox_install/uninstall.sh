#!/usr/bin/env bash
# uninstall.sh — чисте видалення Infrabox з вузла
#
# Usage:
#   bash uninstall.sh                    → інтерактивно (питає про critical дані)
#   bash uninstall.sh --keep-data        → тільки зупинити контейнери
#   bash uninstall.sh --full             → видалити все (з підтвердженням)
#   bash uninstall.sh --full --force     → видалити все без питань
#   bash uninstall.sh ui arch            → тільки вказані підсистеми
#   bash uninstall.sh --topology file    → альтернативний topology файл
#
# Режими:
#   default   — compose down + видалення некритичних даних + питання про critical
#   --keep-data — тільки compose down, дані не чіпаємо
#   --full    — compose down + видалення ВСІХ даних + мережа + deploy_dir
#
# Порядок: відповідно до undeploy_order в topology.yml

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
MODE="interactive"   # interactive | keep-data | full
FORCE=0
FILTER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --keep-data)   MODE="keep-data";  shift   ;;
        --full)        MODE="full";       shift   ;;
        --force)       FORCE=1;           shift   ;;
        --topology)    TOPOLOGY="$2";     shift 2 ;;
        --*) fail "Невідомий аргумент: $1" ;;
        *)   FILTER="${FILTER:+$FILTER }$1"; shift ;;
    esac
done

[ -f "$TOPOLOGY" ] || fail "Не знайдено: $TOPOLOGY"
command -v python3 &>/dev/null || fail "python3 не знайдено"

# ── Парсинг topology.yml ──────────────────────────────────────────────────────
_parse() {
python3 - "$TOPOLOGY" <<'PY'
import sys, json

with open(sys.argv[1]) as f:
    raw = f.read()

def unquote(s):
    s = s.strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 0 else s[1:]
    if s.startswith("'"):
        end = s.find("'", 1)
        return s[1:end] if end > 0 else s[1:]
    if '  #' in s:
        s = s[:s.index('  #')].strip()
    elif ' #' in s:
        s = s[:s.index(' #')].strip()
    sl = s.lower()
    if sl in ('true', 'yes'):  return True
    if sl in ('false', 'no'):  return False
    return s

def indent(line):
    return len(line) - len(line.lstrip(' '))

data = {
    'system':         {},
    'nodes':          {},
    'subsystems':     {},
    'deploy_order':   [],
    'undeploy_order': [],
    'data':           [],
}

ctx0     = None
ctx1     = None
cur_data = None   # current data list item

for line in raw.split('\n'):
    s = line.rstrip()
    if not s or s.lstrip().startswith('#'):
        continue

    lvl  = indent(s)
    text = s.strip()

    if lvl == 0:
        ctx0 = text.rstrip(':').strip()
        ctx1 = None
        cur_data = None
        continue

    if lvl == 2:
        if text.startswith('- '):
            rest = text[2:].strip()
            if ctx0 == 'deploy_order':
                data['deploy_order'].append(rest.strip('"'))
            elif ctx0 == 'undeploy_order':
                data['undeploy_order'].append(rest.strip('"'))
            elif ctx0 == 'data':
                cur_data = {}
                data['data'].append(cur_data)
                if ':' in rest:
                    k, _, v = rest.partition(':')
                    cur_data[k.strip()] = unquote(v)
        elif ':' in text:
            k, _, v = text.partition(':')
            k = k.strip(); v = unquote(v)
            if ctx0 == 'system':
                data['system'][k] = v
            elif ctx0 in ('nodes', 'subsystems'):
                ctx1 = k
                data[ctx0][k] = {}
        continue

    if lvl == 4 and ':' in text:
        k, _, v = text.partition(':')
        k = k.strip(); v = unquote(v)
        if ctx0 == 'nodes' and ctx1:
            data['nodes'][ctx1][k] = v
        elif ctx0 == 'subsystems' and ctx1:
            data['subsystems'][ctx1][k] = v
        elif ctx0 == 'data' and cur_data is not None:
            cur_data[k] = v

# ── Shell-змінні ─────────────────────────────────────────────────────────────
if not data['nodes']:
    print("echo 'ERROR: nodes not found' >&2; exit 1"); sys.exit(0)

alias  = list(data['nodes'].keys())[0]
node   = data['nodes'][alias]
system = data['system']

def q(v): return json.dumps(str(v))

print(f'NODE_ALIAS={q(alias)}')
print(f'NODE_HOST={q(node.get("host",""))}')
print(f'NODE_USER={q(node.get("user",""))}')
print(f'NODE_KEY={q(node.get("ssh_key","~/.ssh/id_ed25519"))}')
print(f'NODE_DEPLOY_DIR={q(node.get("deploy_dir",""))}')
print(f'SYS_BRANCH={q(system.get("branch","main"))}')

undeploy = data.get('undeploy_order', []) or list(reversed(data.get('deploy_order', [])))
print(f'UNDEPLOY_ORDER={q(" ".join(undeploy))}')

for name, sub in data['subsystems'].items():
    var = 'SUB_' + name.upper().replace('-','_') + '_WORKDIR'
    print(f'{var}={q(sub.get("workdir", name))}')

# Data items — окремо для volumes і bind mounts
vols_crit=[];  vols_all=[]
bind_crit=[];  bind_all=[]
for item in data['data']:
    t    = item.get('type','')
    crit = bool(item.get('critical', False))
    if t == 'docker_volume':
        name_v = item.get('name','')
        backup = item.get('backup_cmd','')
        entry  = json.dumps(f'{name_v}|||{backup}')
        vols_all.append(entry)
        if crit: vols_crit.append(entry)
    elif t == 'bind':
        path_v = item.get('path','')
        desc   = item.get('description','')
        entry  = json.dumps(f'{path_v}|||{desc}')
        bind_all.append(entry)
        if crit: bind_crit.append(entry)

print(f'DATA_VOLS_ALL={json.dumps(" ".join(vols_all))}')
print(f'DATA_VOLS_CRIT={json.dumps(" ".join(vols_crit))}')
print(f'DATA_BIND_ALL={json.dumps(" ".join(bind_all))}')
print(f'DATA_BIND_CRIT={json.dumps(" ".join(bind_crit))}')

PY
}

TOPO_VARS=$(_parse)
[ -z "$TOPO_VARS" ] && fail "Не вдалося розпарсити $TOPOLOGY"
eval "$TOPO_VARS"

# ── Фільтр підсистем ──────────────────────────────────────────────────────────
if [ -n "$FILTER" ]; then
    ACTIVE_SUBS=""
    for s in $UNDEPLOY_ORDER; do
        for f in $FILTER; do
            [ "$s" = "$f" ] && ACTIVE_SUBS="${ACTIVE_SUBS:+$ACTIVE_SUBS }$s" && break
        done
    done
    [ -z "$ACTIVE_SUBS" ] && fail "Жодна підсистема не відповідає фільтру: $FILTER"
    PARTIAL=1
else
    ACTIVE_SUBS="$UNDEPLOY_ORDER"
    PARTIAL=0
fi

# ── SSH-хелпери ───────────────────────────────────────────────────────────────
KEY_PATH="${NODE_KEY/#\~/$HOME}"
_ssh_opt() {
    [ -f "$KEY_PATH" ] && echo "-i $KEY_PATH -o StrictHostKeyChecking=accept-new" \
                       || echo "-o StrictHostKeyChecking=accept-new"
}
_ssh()   { ssh $(_ssh_opt) "${NODE_USER}@${NODE_HOST}" "$@"; }
_ssh_q() { _ssh "$@" 2>/dev/null || true; }

# ── Підтвердження ──────────────────────────────────────────────────────────────
confirm() {
    # confirm "Питання?" → 0=yes 1=no
    [ "$FORCE" = "1" ] && return 0
    printf "  ${Y}%s${N} [y/N]: " "$1"
    read -r ans || true
    case "$ans" in y|Y|yes|YES|т|Т|так|ТАК) return 0 ;; *) return 1 ;; esac
}

# ── Заголовок ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${R}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${R}║             Infrabox  Uninstall                      ║${N}"
echo -e "${R}╚══════════════════════════════════════════════════════╝${N}"
echo ""
printf "  Вузол:   ${G}%s${N}  (%s)\n" "$NODE_ALIAS" "$NODE_HOST"
printf "  Deploy:  %s\n"               "$NODE_DEPLOY_DIR"
printf "  Режим:   ${Y}%s${N}\n"        "$MODE"
printf "  Порядок: %s\n"               "$ACTIVE_SUBS"
echo ""

case "$MODE" in
    keep-data) info "Тільки зупинка контейнерів. Дані не видаляються." ;;
    full)      warn "FULL режим — будуть видалені ВСІ дані і директорія ${NODE_DEPLOY_DIR}" ;;
    *)         info "Інтерактивний режим — буде запит перед видаленням критичних даних." ;;
esac
echo ""

# Загальне підтвердження
if [ "$MODE" = "full" ]; then
    confirm "Продовжити видалення всього на ${NODE_HOST}?" || { warn "Скасовано."; exit 0; }
else
    confirm "Продовжити?" || { warn "Скасовано."; exit 0; }
fi

# ── SSH-перевірка ─────────────────────────────────────────────────────────────
step "SSH"
if _ssh_q "exit 0"; then
    ok "SSH OK: ${NODE_USER}@${NODE_HOST}"
else
    fail "SSH недоступний: ${NODE_USER}@${NODE_HOST}"
fi

# ── Docker compose down ───────────────────────────────────────────────────────
step "Docker compose down"

for SUB in $ACTIVE_SUBS; do
    WD_VAR="SUB_$(echo "$SUB" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_WORKDIR"
    WORKDIR="${!WD_VAR:-$SUB}"
    COMPOSE_DIR="${NODE_DEPLOY_DIR}/${WORKDIR}"

    printf "\n  ${B}[%s]${N}  %s\n" "$SUB" "$COMPOSE_DIR"

    HAS_COMPOSE=$(_ssh_q \
        "test -f '${COMPOSE_DIR}/docker-compose.yml' \
      || test -f '${COMPOSE_DIR}/compose.yml' \
      && echo yes || echo no")

    if [ "${HAS_COMPOSE:-no}" != "yes" ]; then
        warn "  Пропускаємо: compose-файл не знайдено у ${COMPOSE_DIR}"
        continue
    fi

    DOWN_OUT=$(_ssh "cd '${COMPOSE_DIR}' && docker compose down 2>&1") || true
    if echo "$DOWN_OUT" | grep -qi "error\b"; then
        echo "$DOWN_OUT" | grep -i error | sed 's/^/    /'
        warn "  compose down завершився з помилками (продовжуємо)"
    else
        ok "  ${SUB}: down ✓"
    fi
done

# ── Дані — bind mounts ────────────────────────────────────────────────────────
if [ "$MODE" != "keep-data" ] && [ "$PARTIAL" = "0" ]; then

    step "Bind mounts"

    # Некритичні — видаляємо без питань
    for ENTRY in $DATA_BIND_ALL; do
        PATH_REL="${ENTRY%%|||*}"
        DESC="${ENTRY##*|||}"
        IS_CRIT=0
        for CE in $DATA_BIND_CRIT; do
            [ "${CE%%|||*}" = "$PATH_REL" ] && IS_CRIT=1 && break
        done

        FULL_PATH="${NODE_DEPLOY_DIR}/${PATH_REL}"

        if [ "$IS_CRIT" = "0" ]; then
            _ssh_q "rm -rf '${FULL_PATH}'" || true
            ok "Видалено (некритичне): ${PATH_REL}"
        fi
    done

    # Критичні — питаємо (або --full їх включає)
    for ENTRY in $DATA_BIND_CRIT; do
        PATH_REL="${ENTRY%%|||*}"
        DESC="${ENTRY##*|||}"
        FULL_PATH="${NODE_DEPLOY_DIR}/${PATH_REL}"

        echo ""
        printf "  ${Y}CRITICAL${N}: %s\n" "$PATH_REL"
        printf "           %s\n" "$DESC"

        if [ "$MODE" = "full" ] || confirm "  Видалити ${PATH_REL}?"; then
            _ssh_q "rm -rf '${FULL_PATH}'" || true
            ok "Видалено: ${PATH_REL}"
        else
            info "Збережено: ${PATH_REL}"
        fi
    done

fi

# ── Дані — Docker volumes ─────────────────────────────────────────────────────
if [ "$MODE" != "keep-data" ] && [ "$PARTIAL" = "0" ] && [ -n "${DATA_VOLS_ALL:-}" ]; then

    step "Docker volumes"

    for ENTRY in $DATA_VOLS_ALL; do
        VOL_NAME="${ENTRY%%|||*}"
        BACKUP_CMD="${ENTRY##*|||}"
        IS_CRIT=0
        for CE in $DATA_VOLS_CRIT; do
            [ "${CE%%|||*}" = "$VOL_NAME" ] && IS_CRIT=1 && break
        done

        # Перевірити чи існує volume
        EXISTS=$(_ssh_q "docker volume inspect '${VOL_NAME}' &>/dev/null && echo yes || echo no")

        if [ "${EXISTS:-no}" != "yes" ]; then
            info "Volume ${VOL_NAME}: не існує, пропускаємо"
            continue
        fi

        echo ""
        printf "  ${Y}%sVolume${N}: %s\n" "$([ "$IS_CRIT" = "1" ] && echo "CRITICAL " || echo "")" "$VOL_NAME"

        if [ -n "$BACKUP_CMD" ]; then
            printf "  ${B}Бекап:${N}  %s\n" "$BACKUP_CMD"
        fi

        if [ "$MODE" = "full" ] || [ "$IS_CRIT" = "0" ] || confirm "  Видалити volume ${VOL_NAME}?"; then
            _ssh_q "docker volume rm --force '${VOL_NAME}'" || true
            ok "Volume видалено: ${VOL_NAME}"
        else
            info "Volume збережено: ${VOL_NAME}"
        fi
    done

fi

# ── Docker мережа ─────────────────────────────────────────────────────────────
if [ "$MODE" != "keep-data" ] && [ "$PARTIAL" = "0" ]; then

    step "Docker мережа"

    NET_EXISTS=$(_ssh_q "docker network inspect infrabox-net &>/dev/null && echo yes || echo no")
    if [ "${NET_EXISTS:-no}" = "yes" ]; then
        _ssh_q "docker network rm infrabox-net" || true
        ok "Мережу infrabox-net видалено"
    else
        info "Мережа infrabox-net: вже відсутня"
    fi

fi

# ── Deploy directory ──────────────────────────────────────────────────────────
if [ "$MODE" = "full" ] && [ "$PARTIAL" = "0" ]; then

    step "Deploy directory"

    echo ""
    printf "  ${R}УВАГА${N}: видалення директорії ${NODE_DEPLOY_DIR}\n"

    if confirm "  Видалити ${NODE_DEPLOY_DIR}?"; then
        _ssh_q "rm -rf '${NODE_DEPLOY_DIR}'" || true
        ok "Директорію видалено: ${NODE_DEPLOY_DIR}"
    else
        info "Директорію збережено: ${NODE_DEPLOY_DIR}"
    fi

fi

# ── Logrotate (потребує sudo на вузлі) ───────────────────────────────────────
if [ "$MODE" = "full" ] && [ "$PARTIAL" = "0" ]; then

    step "Logrotate"

    LR_EXISTS=$(_ssh_q "test -f /etc/logrotate.d/infrabox && echo yes || echo no")
    if [ "${LR_EXISTS:-no}" = "yes" ]; then
        # sudo потрібен — просто інформуємо
        warn "Видаліть вручну на ${NODE_HOST}:"
        warn "  sudo rm /etc/logrotate.d/infrabox"
    else
        info "Logrotate config: вже відсутній"
    fi

fi

# ── Залишкові контейнери ──────────────────────────────────────────────────────
step "Перевірка залишків"

LEFTOVERS=$(_ssh_q "docker ps -a --format '{{.Names}}' | grep -i infrabox" || true)
if [ -n "$LEFTOVERS" ]; then
    warn "Залишились контейнери infrabox:"
    echo "$LEFTOVERS" | sed 's/^/    /'
else
    ok "Контейнери infrabox відсутні"
fi

# ── Підсумок ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  Видалення завершено ✓${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
printf "  Вузол:  %s  (%s)\n" "$NODE_ALIAS" "$NODE_HOST"
printf "  Режим:  %s\n"       "$MODE"
printf "  Знято:  %s\n"       "$ACTIVE_SUBS"
echo ""
