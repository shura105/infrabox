#!/usr/bin/env bash
# 0_probe.sh — роль-орієнтована оцінка цільового хоста для Infrabox
#
# ПЕРШИЙ крок. Запуск НА ЦІЛЬОВОМУ ХОСТІ (Linux або macOS). Тонкий автономний
# збирач — нічого не змінює, тільки читає. Працює навіть за обмеженого доступу.
#
#   bash 0_probe.sh                           → інтерактивно: меню вибору ролей
#   bash 0_probe.sh --role core,arch,ui,adm   → без меню (неінтерактивно)
#   bash 0_probe.sh --role arch --json        → тільки JSON у stdout
#
# Результат — host-report.json (факти + наміри). Принесіть звіти на admin-машину
# і запустіть 1_prepare.sh — воно збере topology.yml зі звітів.
#
# Вердикт по кожній ролі — ДОРАДЧИЙ (warn не блокує; fail лише на жорстких вимогах).
# Платформа: core/adm — лише Linux; ui/arch — будь-яка ОС з Docker.

OUTPUT_FILE="host-report.json"
JSON_ONLY=0
ROLES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --role)  ROLES="$2"; shift 2 ;;
        --json)  JSON_ONLY=1; shift ;;
        *) echo "Невідомий аргумент: $1" >&2; exit 1 ;;
    esac
done
ROLES=$(echo "$ROLES" | tr ',' ' ')

# ── Кольори (людський вивід → stderr) ─────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; D='\033[2m'; N='\033[0m'
say()  { [ "$JSON_ONLY" = "1" ] || echo -e "$*" >&2; }
ok()   { say "  ${G}✓${N} $*"; }
wn()   { say "  ${Y}!${N} $*"; }
er()   { say "  ${R}✗${N} $*"; }

# ── Утиліти ───────────────────────────────────────────────────────────────────
_cmd()  { command -v "$1" >/dev/null 2>&1; }
_esc()  { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
_try()  { "$@" 2>/dev/null || true; }
_trim() { echo "$1" | xargs; }

# ── Інтерактивний вибір ролей (якщо не задано --role і не --json) ─────────────
# Роль можна задати й параметром (--role) — для неінтерактивного запуску з
# неінтерактивного запуску. Без параметра скрипт питає сам.
if [ -z "$ROLES" ] && [ "$JSON_ONLY" = "0" ]; then
    {
        echo ""
        echo -e "${C}Які підсистеми плануються на цей хост?${N}"
        echo "  1) core    (Redis, MQTT, auth, simulator, selfdiag)  — лише Linux"
        echo "  2) adm     (адмін-сервіс: контейнери, хост)          — лише Linux"
        echo "  3) ui      (web + backend API)                       — будь-яка ОС"
        echo "  4) arch    (архіватор історії)                       — будь-яка ОС"
        echo "  (кілька через пробіл: напр. «3 4» або «ui arch»; Enter — без ролей)"
        printf "  Вибір: "
    } >&2
    read -r _sel || true
    for tok in $_sel; do
        case "$tok" in
            1|core) ROLES="$ROLES core" ;;
            2|adm)  ROLES="$ROLES adm"  ;;
            3|ui)   ROLES="$ROLES ui"   ;;
            4|arch) ROLES="$ROLES arch" ;;
            *) echo "  ! пропущено невідоме: $tok" >&2 ;;
        esac
    done
    ROLES=$(echo "$ROLES" | xargs)
fi

# ── Платформа ──────────────────────────────────────────────────────────────────
UNAME_S=$(uname -s)
case "$UNAME_S" in
    Linux)  PLATFORM="linux"  ;;
    Darwin) PLATFORM="macos"  ;;
    *) echo "0_probe.sh підтримує Linux і macOS (поточна: $UNAME_S)" >&2; exit 1 ;;
esac

# ── HARDWARE ───────────────────────────────────────────────────────────────────
hw_arch=$(_try uname -m)
if [ "$PLATFORM" = "linux" ]; then
    hw_cpu_count=$(_try nproc)
    hw_cpu_model=$(_trim "$(_try grep -m1 'model name\|Model name\|Processor' /proc/cpuinfo | cut -d: -f2)")
    hw_ram_mb=$(_try awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
    hw_board=""
    for f in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
        [ -f "$f" ] && hw_board=$(_try cat "$f" | tr -d '\0') && break
    done
else
    hw_cpu_count=$(_try sysctl -n hw.ncpu)
    hw_cpu_model=$(_try sysctl -n machdep.cpu.brand_string)
    hw_ram_mb=$(_try sysctl -n hw.memsize | awk '{printf "%d", $1/1024/1024}')
    hw_board=$(_try sysctl -n hw.model)
fi

# ── OS ─────────────────────────────────────────────────────────────────────────
os_id="" os_version="" os_pretty="" os_like="" os_systemd=""
if [ "$PLATFORM" = "linux" ]; then
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        os_id="${ID:-}"; os_version="${VERSION_ID:-}"; os_pretty="${PRETTY_NAME:-}"; os_like="${ID_LIKE:-}"
    fi
    _cmd systemctl && os_systemd=$(_try systemctl --version | head -1 | awk '{print $2}')
else
    os_id="macos"; os_version=$(_try sw_vers -productVersion); os_pretty="macOS ${os_version}"
fi
os_kernel=$(_try uname -r)
os_bits=$(_try getconf LONG_BIT)

# kernel major.minor (для WG ≥5.6 на Linux)
KVER_MAJ=$(echo "$os_kernel" | cut -d. -f1)
KVER_MIN=$(echo "$os_kernel" | cut -d. -f2)
kernel_ge_56=0
if [ "${KVER_MAJ:-0}" -gt 5 ] 2>/dev/null; then kernel_ge_56=1
elif [ "${KVER_MAJ:-0}" -eq 5 ] 2>/dev/null && [ "${KVER_MIN:-0}" -ge 6 ] 2>/dev/null; then kernel_ge_56=1; fi

# ── NETWORK: primary iface (default route, без залежності від інтернету) ──────
if [ "$PLATFORM" = "linux" ]; then
    primary_iface=$(_try ip route show default | awk '{for(i=1;i<=NF;i++)if($i=="dev")print $(i+1)}' | head -1)
    if [ -z "$primary_iface" ]; then
        for ifc in $(_try ls /sys/class/net); do
            case "$ifc" in lo|docker*|br-*|veth*|wg*|tun*|tap*|zram*) continue ;; esac
            readlink -f "/sys/class/net/$ifc" 2>/dev/null | grep -q '/virtual/' && continue
            ip -4 addr show "$ifc" 2>/dev/null | grep -q 'inet ' && primary_iface="$ifc" && break
        done
    fi
    primary_ip=$(_try ip -4 addr show "$primary_iface" | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)
    net_gateway=$(_try ip route show default | awk '{print $3}' | head -1)
else
    primary_iface=$(_try route -n get default | awk '/interface:/{print $2}')
    primary_ip=$(_try ifconfig "$primary_iface" | awk '/inet /{print $2}' | head -1)
    net_gateway=$(_try route -n get default | awk '/gateway:/{print $2}')
fi
net_hostname=$(_try hostname | sed 's/\.local$//')
net_fqdn=$(_try hostname -f 2>/dev/null)

# mDNS-ім'я
mdns_active=0
if [ "$PLATFORM" = "linux" ]; then
    { _cmd systemctl && [ "$(_try systemctl is-active avahi-daemon)" = "active" ]; } && mdns_active=1
else
    mdns_active=1   # macOS Bonjour — завжди
fi
net_mdns_name=""
[ "$mdns_active" = "1" ] && net_mdns_name="${net_hostname}.local"

# всі IPv4-інтерфейси (з позначкою physical)
net_ifaces_json="["; first=1
if [ "$PLATFORM" = "linux" ]; then
    for ifc in $(_try ls /sys/class/net); do
        [ "$ifc" = "lo" ] && continue
        ip4=$(_try ip -4 addr show "$ifc" | awk '/inet /{print $2}' | head -1)
        [ -z "$ip4" ] && continue
        phys=false
        readlink -f "/sys/class/net/$ifc" 2>/dev/null | grep -q '/virtual/' || phys=true
        [ "$first" = "1" ] && first=0 || net_ifaces_json+=","
        net_ifaces_json+="{\"iface\":\"$(_esc "$ifc")\",\"ip\":\"$(_esc "$ip4")\",\"physical\":$phys}"
    done
else
    for ifc in $(_try ifconfig -l); do
        case "$ifc" in lo*|bridge*|utun*|gif*|stf*|llw*|awdl*|ap*) continue ;; esac
        ip4=$(_try ifconfig "$ifc" | awk '/inet /{print $2}' | head -1)
        [ -z "$ip4" ] && continue
        [ "$first" = "1" ] && first=0 || net_ifaces_json+=","
        net_ifaces_json+="{\"iface\":\"$(_esc "$ifc")\",\"ip\":\"$(_esc "$ip4")\",\"physical\":true}"
    done
fi
net_ifaces_json+="]"

# ── STORAGE: тип носія root-пристрою ──────────────────────────────────────────
if [ "$PLATFORM" = "linux" ]; then
    root_src=$(_try findmnt -no SOURCE /)
    root_disk=$(_try lsblk -no PKNAME "$root_src" | head -1)
    [ -z "$root_disk" ] && root_disk=$(echo "$root_src" | sed 's|/dev/||; s|p\?[0-9]*$||')
    detect_storage_type() {
        case "$1" in
            mmcblk*) echo "sd" ;;          # SD-карта / eMMC
            nvme*)   echo "ssd" ;;
            "")      echo "unknown" ;;
            *) local rota; rota=$(_try lsblk -dno ROTA "/dev/$1" | tr -d ' ')
               [ "$rota" = "0" ] && echo "ssd" || echo "hdd" ;;
        esac
    }
    storage_type=$(detect_storage_type "$root_disk")
    storage_free_gb=$(_try df -BG / | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
    swap_mb=$(_try free -m | awk '/^Swap/{print $2}')
else
    root_disk=$(_try df / | awk 'NR==2{print $1}' | sed 's|/dev/||')
    storage_type="ssd"   # сучасні Mac — внутрішній SSD/NVMe
    storage_free_gb=$(_try df -g / | awk 'NR==2{print $4+0}')
    swap_mb=$(_try sysctl -n vm.swapusage | awk '{gsub("M","",$3); print int($3)}')
fi

# ── WIREGUARD ──────────────────────────────────────────────────────────────────
wg_tools=false; _cmd wg && wg_tools=true
wg_quick=false; _cmd wg-quick && wg_quick=true
if [ "$PLATFORM" = "linux" ]; then
    wg_module=0
    { ls /sys/module/wireguard >/dev/null 2>&1 || _try modinfo wireguard >/dev/null; } && wg_module=1
    wg_kernel_ok=0; { [ "$kernel_ge_56" = "1" ] || [ "$wg_module" = "1" ]; } && wg_kernel_ok=1
    wg_udp_free=true; _try ss -uln | grep -q ':51820 ' && wg_udp_free=false
    ip_forward=$(_try sysctl -n net.ipv4.ip_forward); [ -z "$ip_forward" ] && ip_forward=$(_try cat /proc/sys/net/ipv4/ip_forward)
else
    wg_module=0   # macOS — userspace (wireguard-go), kernel-модуля нема
    wg_kernel_ok=0; [ "$wg_tools" = "true" ] && wg_kernel_ok=1   # готовність = wg-tools (userspace)
    wg_udp_free=true; _try lsof -nP -iUDP:51820 2>/dev/null | grep -q . && wg_udp_free=false
    ip_forward=$(_try sysctl -n net.inet.ip.forwarding)
fi

# ── TIME ───────────────────────────────────────────────────────────────────────
ntp_synced=false
if [ "$PLATFORM" = "linux" ]; then
    [ "$(_try timedatectl show -p NTPSynchronized --value)" = "yes" ] && ntp_synced=true
    tz=$(_try timedatectl show -p Timezone --value)
else
    _try pgrep -x timed >/dev/null && ntp_synced=true   # timed працює → Apple NTP
    tz=$(_try readlink /etc/localtime | sed 's|.*/zoneinfo/||')
fi

# ── READINESS ──────────────────────────────────────────────────────────────────
docker_running=false
if [ "$PLATFORM" = "linux" ]; then
    [ "$(_try systemctl is-active docker)" = "active" ] && docker_running=true
else
    _try docker info >/dev/null 2>&1 && docker_running=true   # Docker Desktop
fi
docker_ver=$(_try docker info --format '{{.ServerVersion}}'); [ -z "$docker_ver" ] && docker_ver=$(_try docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
docker_storage=$(_try docker info --format '{{.Driver}}')
compose_ver=$(_try docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
internet=false
_try curl -sI --max-time 5 https://github.com >/dev/null 2>&1 && internet=true
if [ "$PLATFORM" = "linux" ]; then
    pkg_mgr=""; for m in apt-get apt dnf yum pacman apk; do _cmd "$m" && pkg_mgr="$m" && break; done
    firewall="none"
    if _cmd ufw && [ "$(_try ufw status | head -1)" = "Status: active" ]; then firewall="ufw"
    elif _cmd nft && [ -n "$(_try nft list ruleset)" ]; then firewall="nftables"
    elif _cmd iptables && [ -n "$(_try iptables -S 2>/dev/null | grep -v '^-P')" ]; then firewall="iptables"; fi
else
    pkg_mgr=""; _cmd brew && pkg_mgr="brew"
    firewall="none"
    _try /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -qi enabled && firewall="appfw"
fi

# ── SOFTWARE ───────────────────────────────────────────────────────────────────
sw_python=$(_try python3 --version | awk '{print $2}')
sw_git=$(_try git --version | awk '{print $3}')
b_curl=$(_cmd curl && echo true || echo false)
b_wget=$(_cmd wget && echo true || echo false)
b_rsync=$(_cmd rsync && echo true || echo false)
b_jq=$(_cmd jq && echo true || echo false)

# ── USER ───────────────────────────────────────────────────────────────────────
usr_name=$(_try id -un)
usr_uid=$(_try id -u)
usr_groups=$(_try id -Gn | tr ' ' ',')
usr_sudo="none"
if _cmd sudo; then _try sudo -n true && usr_sudo="nopasswd" || usr_sudo="available"; fi
usr_in_docker=false; _try id -nG | grep -qw docker && usr_in_docker=true

# ── PORTS (TCP) ────────────────────────────────────────────────────────────────
check_port() {
    local port="$1" busy=""
    if [ "$PLATFORM" = "linux" ]; then
        _cmd ss && busy=$(_try ss -tlnp "sport = :$port" | awk 'NR>1{print $NF}' | head -1)
    else
        busy=$(_try lsof -nP -iTCP:"$port" -sTCP:LISTEN | awk 'NR>1{print $1}' | head -1)
    fi
    [ -n "$busy" ] && echo "false" || echo "true"
}
P80=$(check_port 80); P443=$(check_port 443)
P1883=$(check_port 1883); P1884=$(check_port 1884); P6379=$(check_port 6379)

# ══════════════════════════════════════════════════════════════════════════════
# РОЛЬ-ПЕРЕВІРКИ
# ══════════════════════════════════════════════════════════════════════════════
CHECKS_JSON=""; CHECKS_HUMAN=""; VERDICT="ok"
reset_checks() { CHECKS_JSON=""; CHECKS_HUMAN=""; VERDICT="ok"; }
add_check() {
    # add_check <name> <status ok|warn|fail> <detail>
    local c="{\"name\":\"$1\",\"status\":\"$2\",\"detail\":\"$(_esc "$3")\"}"
    CHECKS_JSON="${CHECKS_JSON:+$CHECKS_JSON,}$c"
    # людський рядок паралельно (newline-safe; JSON по комі парсити не можна)
    CHECKS_HUMAN="${CHECKS_HUMAN}$2|$1|$3
"
    [ "$2" = "fail" ] && VERDICT="fail"
    [ "$2" = "warn" ] && [ "$VERDICT" != "fail" ] && VERDICT="warn"
    return 0   # ЗАВЖДИ success — інакше `&& add_check ok || add_check warn` задвоює виклик
}

# спільні для всіх вузлів тунелю
check_common() {
    if [ "$wg_kernel_ok" = "1" ] && [ "$wg_tools" = "true" ]; then
        add_check "wireguard" "ok" "готовий (wg-tools + ядро/userspace)"
    elif [ "$wg_tools" = "true" ]; then
        add_check "wireguard" "warn" "wg-tools є, ядро/модуль не підтверджено"
    else
        add_check "wireguard" "warn" "wg-tools не встановлено — потрібен WG"
    fi
    [ "$ntp_synced" = "true" ] && add_check "ntp" "ok" "час синхронізовано" \
                               || add_check "ntp" "warn" "час НЕ синхронізовано (desync_guard)"
    [ "$docker_running" = "true" ] && add_check "docker" "ok" "daemon active ($docker_ver)" \
                                   || add_check "docker" "warn" "docker не запущено (host-prep встановить)"
    [ "$internet" = "true" ] && add_check "internet" "ok" "github досяжний" \
                             || add_check "internet" "warn" "немає доступу до github (clone/pull/apt)"
}

check_core() {
    reset_checks
    [ "$PLATFORM" != "linux" ] && add_check "platform" "fail" \
        "core потребує Linux — selfdiag міряє метрики хоста (/proc,/sys); на Docker Desktop це ВМ"
    [ "${hw_ram_mb:-0}" -ge 512 ] 2>/dev/null && add_check "ram" "ok" "${hw_ram_mb}MB" \
                                              || add_check "ram" "warn" "${hw_ram_mb}MB (<512)"
    [ "$P1883" = "true" ] && add_check "port_1883" "ok" "MQTT real вільний" \
                          || add_check "port_1883" "warn" "1883 зайнятий"
    [ "$P1884" = "true" ] && add_check "port_1884" "ok" "MQTT sim вільний" \
                          || add_check "port_1884" "warn" "1884 зайнятий"
    [ "$mdns_active" = "1" ] && add_check "mdns" "ok" "mDNS активний (.local)" \
                             || add_check "mdns" "warn" "mDNS неактивний — вузли не знайдуть core по .local"
    check_common
}

check_arch() {
    reset_checks
    case "$storage_type" in
        ssd|hdd) add_check "storage" "ok" "${storage_type} (${storage_free_gb}GB) — придатний для архіву" ;;
        sd|emmc) add_check "storage" "warn" "SD/eMMC — інтенсивний запис зношує; рекомендовано SSD/HDD" ;;
        *)       add_check "storage" "warn" "тип носія невідомий" ;;
    esac
    [ "${storage_free_gb:-0}" -ge 5 ] 2>/dev/null && add_check "disk_free" "ok" "${storage_free_gb}GB" \
                                                  || add_check "disk_free" "warn" "${storage_free_gb}GB (<5)"
    [ "${hw_ram_mb:-0}" -ge 512 ] 2>/dev/null && add_check "ram" "ok" "${hw_ram_mb}MB (tmpfs-буфер)" \
                                              || add_check "ram" "warn" "${hw_ram_mb}MB (<512, tmpfs-буфер)"
    check_common
}

check_ui() {
    reset_checks
    [ "$P80" = "true" ]  && add_check "port_80" "ok" "HTTP вільний"   || add_check "port_80" "warn" "80 зайнятий"
    [ "$P443" = "true" ] && add_check "port_443" "ok" "HTTPS вільний" || add_check "port_443" "warn" "443 зайнятий"
    check_common
}

check_adm() {
    reset_checks
    [ "$PLATFORM" != "linux" ] && add_check "platform" "fail" \
        "adm потребує Linux — pid:host і керування хостом (reboot/shutdown) на Docker Desktop недоступні"
    if [ -S /var/run/docker.sock ]; then add_check "docker_sock" "ok" "/var/run/docker.sock доступний"
    else add_check "docker_sock" "fail" "немає docker.sock — adm не керуватиме контейнерами"; fi
    [ "$usr_sudo" = "nopasswd" ] && add_check "sudo" "ok" "sudo nopasswd (reboot/shutdown)" \
                                 || add_check "sudo" "warn" "sudo обмежений — керування хостом може не працювати"
    check_common
}

# ── Збірка roles JSON + людський вивід ────────────────────────────────────────
ROLES_JSON=""
say ""
say "${C}━━━ Оцінка хоста: ${net_hostname} (${primary_ip:-?}) ━━━${N}"
say "  ${D}${os_pretty:-$os_id} | ${hw_arch} | ${hw_ram_mb}MB | носій: ${storage_type} ${storage_free_gb}GB${N}"

for role in $ROLES; do
    case "$role" in
        core) check_core ;; arch) check_arch ;; ui) check_ui ;; adm) check_adm ;;
        *) say "  ${Y}!${N} невідома роль: $role"; continue ;;
    esac
    ROLES_JSON="${ROLES_JSON:+$ROLES_JSON,}\"$role\":{\"verdict\":\"$VERDICT\",\"checks\":[$CHECKS_JSON]}"
    # людський вивід вердикту
    case "$VERDICT" in
        ok)   say "\n  ${G}● ${role}: придатний${N}" ;;
        warn) say "\n  ${Y}● ${role}: придатний із застереженнями${N}" ;;
        fail) say "\n  ${R}● ${role}: НЕ придатний${N}" ;;
    esac
    while IFS='|' read -r st nm dt; do
        [ -z "$st" ] && continue
        case "$st" in ok) ok "$nm: $dt";; warn) wn "$nm: $dt";; fail) er "$nm: $dt";; esac
    done <<EOF
$CHECKS_HUMAN
EOF
done

# ── JSON ───────────────────────────────────────────────────────────────────────
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
JSON=$(cat <<ENDJSON
{
  "probe_version": "2",
  "timestamp": "$TIMESTAMP",
  "requested_roles": [$(echo "$ROLES" | tr ' ' '\n' | grep -v '^$' | sed 's/.*/"&"/' | paste -sd, -)],
  "hardware": {
    "arch": "$(_esc "${hw_arch}")",
    "cpu_count": ${hw_cpu_count:-0},
    "cpu_model": "$(_esc "${hw_cpu_model}")",
    "ram_mb": ${hw_ram_mb:-0},
    "board": "$(_esc "${hw_board}")"
  },
  "os": {
    "id": "$(_esc "${os_id}")",
    "version": "$(_esc "${os_version}")",
    "pretty": "$(_esc "${os_pretty}")",
    "id_like": "$(_esc "${os_like}")",
    "kernel": "$(_esc "${os_kernel}")",
    "bits": ${os_bits:-64},
    "systemd_version": "$(_esc "${os_systemd}")"
  },
  "network": {
    "hostname": "$(_esc "${net_hostname}")",
    "fqdn": "$(_esc "${net_fqdn}")",
    "mdns_name": "$(_esc "${net_mdns_name}")",
    "primary_iface": "$(_esc "${primary_iface}")",
    "primary_ip": "$(_esc "${primary_ip}")",
    "gateway": "$(_esc "${net_gateway}")",
    "interfaces": $net_ifaces_json
  },
  "storage": {
    "root_dev": "$(_esc "${root_disk}")",
    "type": "$(_esc "${storage_type}")",
    "free_gb": ${storage_free_gb:-0},
    "swap_mb": ${swap_mb:-0}
  },
  "wireguard": {
    "kernel_ok": $([ "$wg_kernel_ok" = "1" ] && echo true || echo false),
    "module_loaded": $([ "$wg_module" = "1" ] && echo true || echo false),
    "wg_tools": $wg_tools,
    "wg_quick": $wg_quick,
    "udp_51820_free": $wg_udp_free,
    "ip_forward": ${ip_forward:-0}
  },
  "time": {
    "ntp_synced": $ntp_synced,
    "timezone": "$(_esc "${tz}")"
  },
  "readiness": {
    "docker_running": $docker_running,
    "docker_version": "$(_esc "${docker_ver}")",
    "docker_storage": "$(_esc "${docker_storage}")",
    "compose_version": "$(_esc "${compose_ver}")",
    "internet": $internet,
    "pkg_manager": "$(_esc "${pkg_mgr}")",
    "firewall": "$(_esc "${firewall}")"
  },
  "software": {
    "python3": "$(_esc "${sw_python}")",
    "git": "$(_esc "${sw_git}")",
    "curl": $b_curl, "wget": $b_wget, "rsync": $b_rsync, "jq": $b_jq
  },
  "user": {
    "name": "$(_esc "${usr_name}")",
    "uid": ${usr_uid:-0},
    "groups": "$(_esc "${usr_groups}")",
    "sudo": "$(_esc "${usr_sudo}")",
    "in_docker_group": $usr_in_docker
  },
  "ports": {
    "80": $P80, "443": $P443, "1883": $P1883, "1884": $P1884, "6379": $P6379
  },
  "roles": {${ROLES_JSON}}
}
ENDJSON
)

if [ "$JSON_ONLY" = "1" ]; then
    echo "$JSON"
else
    echo "$JSON" > "$OUTPUT_FILE"
    say ""
    say "${G}✓ Збережено: $(pwd)/${OUTPUT_FILE}${N}"
    say ""
    say "${C}Далі:${N} принесіть звіт на admin-машину (scp / флешка) і запустіть"
    say "  ${D}bash 1_prepare.sh host-report.json${N}  (можна кілька звітів — по вузлу)"
fi
