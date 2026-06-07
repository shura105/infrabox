#!/usr/bin/env bash
# 1_probe.sh — роль-орієнтована оцінка цільового хоста для Infrabox (v2)
#
# Запуск НА ЦІЛЬОВОМУ ХОСТІ (Linux). Нічого не змінює — тільки читає.
#
#   bash 1_probe.sh --role core,arch,ui,adm   → оцінка під задані ролі + host-report.json
#   bash 1_probe.sh --role arch --json        → тільки JSON у stdout
#   bash 1_probe.sh                            → базова оцінка без ролей
#
# Вердикт по кожній ролі — ДОРАДЧИЙ (warn не блокує; fail лише на жорстких вимогах).

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

# ── Лише Linux ─────────────────────────────────────────────────────────────────
if [ "$(uname -s)" != "Linux" ]; then
    echo "1_probe.sh v2 підтримує лише Linux-цілі (поточна: $(uname -s))" >&2
    exit 1
fi

# ── HARDWARE ───────────────────────────────────────────────────────────────────
hw_arch=$(_try uname -m)
hw_cpu_count=$(_try nproc)
hw_cpu_model=$(_trim "$(_try grep -m1 'model name\|Model name\|Processor' /proc/cpuinfo | cut -d: -f2)")
hw_ram_mb=$(_try awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
hw_board=""
for f in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
    [ -f "$f" ] && hw_board=$(_try cat "$f" | tr -d '\0') && break
done

# ── OS ─────────────────────────────────────────────────────────────────────────
os_id="" os_version="" os_pretty="" os_like=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    os_id="${ID:-}"; os_version="${VERSION_ID:-}"; os_pretty="${PRETTY_NAME:-}"; os_like="${ID_LIKE:-}"
fi
os_kernel=$(_try uname -r)
os_bits=$(_try getconf LONG_BIT)
os_systemd=""
_cmd systemctl && os_systemd=$(_try systemctl --version | head -1 | awk '{print $2}')

# kernel major.minor як число (для WG ≥5.6)
KVER_MAJ=$(echo "$os_kernel" | cut -d. -f1)
KVER_MIN=$(echo "$os_kernel" | cut -d. -f2)
kernel_ge_56=0
if [ "${KVER_MAJ:-0}" -gt 5 ] 2>/dev/null; then kernel_ge_56=1
elif [ "${KVER_MAJ:-0}" -eq 5 ] 2>/dev/null && [ "${KVER_MIN:-0}" -ge 6 ] 2>/dev/null; then kernel_ge_56=1; fi

# ── NETWORK: primary iface (default route → fallback фізичний) ─────────────────
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
net_hostname=$(_try hostname)
net_fqdn=$(_try hostname -f)
# mDNS-ім'я: hostname.local якщо avahi активний
mdns_active=0
{ _cmd systemctl && [ "$(_try systemctl is-active avahi-daemon)" = "active" ]; } && mdns_active=1
net_mdns_name=""
[ "$mdns_active" = "1" ] && net_mdns_name="${net_hostname}.local"

# всі IPv4-інтерфейси (з позначкою physical)
net_ifaces_json="["; first=1
for ifc in $(_try ls /sys/class/net); do
    [ "$ifc" = "lo" ] && continue
    ip4=$(_try ip -4 addr show "$ifc" | awk '/inet /{print $2}' | head -1)
    [ -z "$ip4" ] && continue
    phys=false
    readlink -f "/sys/class/net/$ifc" 2>/dev/null | grep -q '/virtual/' || phys=true
    [ "$first" = "1" ] && first=0 || net_ifaces_json+=","
    net_ifaces_json+="{\"iface\":\"$(_esc "$ifc")\",\"ip\":\"$(_esc "$ip4")\",\"physical\":$phys}"
done
net_ifaces_json+="]"

# ── STORAGE: тип носія root-пристрою ──────────────────────────────────────────
root_src=$(_try findmnt -no SOURCE /)
root_disk=$(_try lsblk -no PKNAME "$root_src" | head -1)
[ -z "$root_disk" ] && root_disk=$(echo "$root_src" | sed 's|/dev/||; s|p\?[0-9]*$||')
detect_storage_type() {
    case "$1" in
        mmcblk*) echo "sd" ;;          # SD-карта / eMMC
        nvme*)   echo "ssd" ;;
        "")      echo "unknown" ;;
        *)
            local rota; rota=$(_try lsblk -dno ROTA "/dev/$1" | tr -d ' ')
            if [ "$rota" = "0" ]; then echo "ssd"; else echo "hdd"; fi ;;
    esac
}
storage_type=$(detect_storage_type "$root_disk")
storage_free_gb=$(_try df -BG / | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
swap_mb=$(_try free -m | awk '/^Swap/{print $2}')

# ── WIREGUARD ──────────────────────────────────────────────────────────────────
wg_module=0
{ ls /sys/module/wireguard >/dev/null 2>&1 || _try modinfo wireguard >/dev/null; } && wg_module=1
wg_tools=false; _cmd wg && wg_tools=true
wg_quick=false; _cmd wg-quick && wg_quick=true
# готовність: kernel≥5.6 (WG mainline, можливо builtin) АБО модуль; + інструменти
wg_kernel_ok=0; { [ "$kernel_ge_56" = "1" ] || [ "$wg_module" = "1" ]; } && wg_kernel_ok=1
wg_udp_free=true; _try ss -uln | grep -q ':51820 ' && wg_udp_free=false
ip_forward=$(_try sysctl -n net.ipv4.ip_forward); [ -z "$ip_forward" ] && ip_forward=$(_try cat /proc/sys/net/ipv4/ip_forward)

# ── TIME ───────────────────────────────────────────────────────────────────────
ntp_synced=false
[ "$(_try timedatectl show -p NTPSynchronized --value)" = "yes" ] && ntp_synced=true
tz=$(_try timedatectl show -p Timezone --value)

# ── READINESS ──────────────────────────────────────────────────────────────────
docker_running=false
[ "$(_try systemctl is-active docker)" = "active" ] && docker_running=true
docker_ver=$(_try docker info --format '{{.ServerVersion}}'); [ -z "$docker_ver" ] && docker_ver=$(_try docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
docker_storage=$(_try docker info --format '{{.Driver}}')
compose_ver=$(_try docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
internet=false
_try timeout 5 curl -sI https://github.com >/dev/null 2>&1 && internet=true
pkg_mgr=""; for m in apt-get apt dnf yum pacman apk; do _cmd "$m" && pkg_mgr="$m" && break; done
firewall="none"
if _cmd ufw && [ "$(_try ufw status | head -1)" = "Status: active" ]; then firewall="ufw"
elif _cmd nft && [ -n "$(_try nft list ruleset)" ]; then firewall="nftables"
elif _cmd iptables && [ -n "$(_try iptables -S 2>/dev/null | grep -v '^-P')" ]; then firewall="iptables"; fi

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
    if _cmd ss; then busy=$(_try ss -tlnp "sport = :$port" | awk 'NR>1{print $NF}' | head -1); fi
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
        add_check "wireguard" "ok" "kernel + wg-tools готові"
    elif [ "$wg_kernel_ok" = "1" ]; then
        add_check "wireguard" "warn" "kernel OK, але wg-tools не встановлено"
    else
        add_check "wireguard" "warn" "kernel<5.6 і модуль відсутній — потрібен WG"
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
    [ "${hw_ram_mb:-0}" -ge 512 ] 2>/dev/null && add_check "ram" "ok" "${hw_ram_mb}MB" \
                                              || add_check "ram" "warn" "${hw_ram_mb}MB (<512)"
    [ "$P1883" = "true" ] && add_check "port_1883" "ok" "MQTT real вільний" \
                          || add_check "port_1883" "warn" "1883 зайнятий"
    [ "$P1884" = "true" ] && add_check "port_1884" "ok" "MQTT sim вільний" \
                          || add_check "port_1884" "warn" "1884 зайнятий"
    [ "$mdns_active" = "1" ] && add_check "mdns" "ok" "avahi активний (.local)" \
                             || add_check "mdns" "warn" "avahi неактивний — вузли не знайдуть core по .local"
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
fi
