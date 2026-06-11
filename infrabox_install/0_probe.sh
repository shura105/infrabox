#!/usr/bin/env bash
# 0_probe.sh — збір даних про цільовий хост для Infrabox
#
# ПЕРШИЙ крок. Запуск НА ЦІЛЬОВОМУ ХОСТІ (Linux або macOS). Тонкий автономний
# збирач: нічого не змінює, тільки ЗБИРАЄ ФАКТИ про машину. Працює навіть за
# обмеженого доступу. Ролей не питає — що куди ставити, вирішує 1_prepare (адмін).
#
#   bash 0_probe.sh          → зведення фактів + host-report-<hostname>.json
#   bash 0_probe.sh --json   → тільки JSON у stdout
#
# Збирає: платформа, hardware, os, network, storage (тип носія), wireguard,
# час (NTP), готовність (docker/інтернет/firewall), software, user, порти.
# Принесіть host-report-*.json усіх вузлів в одну теку на admin-машині → 1_prepare.

JSON_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --json)  JSON_ONLY=1; shift ;;
        *) echo "Невідомий аргумент: $1" >&2; exit 1 ;;
    esac
done

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

# ── Зведення фактів (людський вивід) ──────────────────────────────────────────
_yn() { [ "$1" = "true" ] && echo "так" || echo "ні"; }
say ""
say "${C}━━━ Вузол: ${net_hostname} (${primary_ip:-?}) ━━━${N}"
say "  ОС:        ${os_pretty:-$os_id}  [${PLATFORM}]"
say "  Apparat:   arch ${hw_arch}, CPU ${hw_cpu_count}, RAM ${hw_ram_mb}MB${hw_board:+, $hw_board}"
say "  Носій:     ${storage_type}, вільно ${storage_free_gb}GB, swap ${swap_mb}MB"
say "  Мережа:    ${primary_iface} ${primary_ip}, gw ${net_gateway:-—}, mDNS ${net_mdns_name:-—}"
say "  Docker:    $([ "$docker_running" = true ] && echo "running ${docker_ver}" || echo "не запущено"), compose ${compose_ver:-—}"
say "  WireGuard: wg-tools $(_yn "$wg_tools"), ip_forward ${ip_forward:-0}, UDP51820 вільний $(_yn "$wg_udp_free")"
say "  Час/мережа: NTP $(_yn "$ntp_synced"), інтернет $(_yn "$internet"), firewall ${firewall}, pkg ${pkg_mgr:-—}"
say "  Порти вільні: 80=$(_yn $P80) 443=$(_yn $P443) 1883=$(_yn $P1883) 1884=$(_yn $P1884) 6379=$(_yn $P6379)"

# ── JSON ───────────────────────────────────────────────────────────────────────
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
JSON=$(cat <<ENDJSON
{
  "probe_version": "2",
  "timestamp": "$TIMESTAMP",
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
  }
}
ENDJSON
)

if [ "$JSON_ONLY" = "1" ]; then
    echo "$JSON"
else
    # ім'я з hostname — щоб звіти різних вузлів не конфліктували в одній папці
    SAFE_HOST=$(printf '%s' "$net_hostname" | tr -cd '[:alnum:]_.-')
    OUTPUT_FILE="host-report-${SAFE_HOST:-host}.json"
    echo "$JSON" > "$OUTPUT_FILE"
    say ""
    say "${G}✓ Збережено: $(pwd)/${OUTPUT_FILE}${N}"
    say ""
    say "${C}Далі:${N} зберіть звіти всіх вузлів в одну теку на admin-машині"
    say "  (scp / флешка), потім запустіть ${D}bash 1_prepare.sh${N} — воно само їх знайде."
fi
