#!/usr/bin/env bash
# probe.sh — збір даних про хост для infrabox-installer
# Запуск: bash probe.sh        → виводить JSON і зберігає host-report.json
#         bash probe.sh --json → тільки JSON у stdout (для pipe)
#
# Нічого не встановлює, нічого не змінює — тільки читає.

OUTPUT_FILE="host-report.json"
JSON_ONLY=0
[ "${1:-}" = "--json" ] && JSON_ONLY=1

# ── утиліти ──────────────────────────────────────────────────────────────────

_cmd()  { command -v "$1" &>/dev/null; }
_esc()  { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
_try()  { "$@" 2>/dev/null || true; }
_trim() { echo "$1" | xargs; }

# Визначення платформи
PLATFORM="linux"
[ "$(uname -s)" = "Darwin" ] && PLATFORM="macos"

# ── апаратура ─────────────────────────────────────────────────────────────────

hw_arch=$(_try uname -m)

if [ "$PLATFORM" = "linux" ]; then
    hw_cpu_count=$(_try nproc)
    hw_cpu_model=$(_try grep -m1 'model name\|Model name\|Processor' /proc/cpuinfo | cut -d: -f2)
    hw_cpu_model=$(_trim "$hw_cpu_model")
    hw_ram_mb=$(_try awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
    hw_disk_free_gb=$(_try df -BG / | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
    hw_board=""
    for f in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
        [ -f "$f" ] && hw_board=$(_try cat "$f" | tr -d '\0') && break
    done
else
    hw_cpu_count=$(_try sysctl -n hw.ncpu)
    hw_cpu_model=$(_try sysctl -n machdep.cpu.brand_string)
    hw_ram_mb=$(_try sysctl -n hw.memsize | awk '{printf "%d", $1/1024/1024}')
    hw_disk_free_gb=$(_try df -g / | awk 'NR==2{print $4+0}')
    hw_board=$(_try sysctl -n hw.model)
fi

# ── операційна система ────────────────────────────────────────────────────────

os_id="" os_version="" os_pretty="" os_systemd=""

if [ "$PLATFORM" = "linux" ] && [ -f /etc/os-release ]; then
    os_id=$(_try     grep '^ID='           /etc/os-release | cut -d= -f2 | tr -d '"')
    os_version=$(_try grep '^VERSION_ID='  /etc/os-release | cut -d= -f2 | tr -d '"')
    os_pretty=$(_try  grep '^PRETTY_NAME=' /etc/os-release | cut -d= -f2 | tr -d '"')
elif [ "$PLATFORM" = "macos" ]; then
    os_id="macos"
    os_version=$(_try sw_vers -productVersion)
    os_pretty="macOS $os_version"
fi

os_kernel=$(_try uname -r)
os_bits=$(_try getconf LONG_BIT)

if _cmd systemctl; then
    os_systemd=$(_try systemctl --version | head -1 | awk '{print $2}')
fi

# ── мережа ───────────────────────────────────────────────────────────────────

net_hostname=$(_try hostname)
net_fqdn=$(_try hostname -f 2>/dev/null || hostname)

# Інтерфейси з IPv4
net_ifaces_json="["
first_iface=1

if [ "$PLATFORM" = "linux" ] && _cmd ip; then
    while IFS= read -r iface; do
        ip4=$(_try ip -4 addr show "$iface" | awk '/inet /{print $2}' | head -1)
        [ -z "$ip4" ] && continue
        mac=$(_try ip link show "$iface" | awk '/link\/ether/{print $2}' | head -1)
        [ "$first_iface" = "1" ] && first_iface=0 || net_ifaces_json+=","
        net_ifaces_json+="{\"iface\":\"$(_esc "$iface")\",\"ip\":\"$(_esc "$ip4")\",\"mac\":\"$(_esc "$mac")\"}"
    done < <(_try ip -o link show | awk -F': ' '$3!~/LOOPBACK/{print $2}' | awk '{print $1}')
elif _cmd ifconfig; then
    while IFS= read -r iface; do
        ip4=$(_try ifconfig "$iface" | awk '/inet /{print $2}' | grep -v '^127\.' | head -1)
        [ -z "$ip4" ] && continue
        mac=$(_try ifconfig "$iface" | awk '/ether/{print $2}' | head -1)
        [ "$first_iface" = "1" ] && first_iface=0 || net_ifaces_json+=","
        net_ifaces_json+="{\"iface\":\"$(_esc "$iface")\",\"ip\":\"$(_esc "$ip4")\",\"mac\":\"$(_esc "$mac")\"}"
    done < <(_try ifconfig -l | tr ' ' '\n' | grep -v '^lo')
fi
net_ifaces_json+="]"

if [ "$PLATFORM" = "linux" ]; then
    net_gateway=$(_try ip route | awk '/^default/{print $3}' | head -1)
else
    net_gateway=$(_try netstat -rn | awk '/^default/{print $2}' | head -1)
fi

# ── зайняті порти (infrabox-специфічні) ──────────────────────────────────────

check_port() {
    local port="$1"
    local busy=""
    if _cmd ss; then
        busy=$(_try ss -tlnp "sport = :$port" | awk 'NR>1{print $7}' | grep -o '[^,/]*$' | head -1)
    elif _cmd netstat; then
        busy=$(_try netstat -tlnp 2>/dev/null | awk -v p=":$port" '$4~p{print $7}' | cut -d/ -f2 | head -1)
        [ -z "$busy" ] && busy=$(_try netstat -an | awk -v p="\.$port " '$0~p && /LISTEN/{print "occupied"}' | head -1)
    fi
    if [ -n "$busy" ]; then
        echo "{\"free\":false,\"process\":\"$(_esc "$busy")\"}"
    else
        echo "{\"free\":true}"
    fi
}

ports_json="{"
first_port=1
for p in 80 443 6379 1883 1884 8099 8100 8101 8102; do
    [ "$first_port" = "1" ] && first_port=0 || ports_json+=","
    ports_json+="\"$p\":$(check_port "$p")"
done
ports_json+="}"

# ── встановлене ПЗ ────────────────────────────────────────────────────────────

sw_docker=""
_cmd docker && sw_docker=$(_try docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)

sw_compose=""
if _cmd docker && docker compose version &>/dev/null; then
    sw_compose=$(_try docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
fi

sw_python=""
_cmd python3 && sw_python=$(_try python3 --version | awk '{print $2}')

sw_git=""
_cmd git && sw_git=$(_try git --version | awk '{print $3}')

sw_curl=$( _cmd curl  && echo "true" || echo "false")
sw_wget=$( _cmd wget  && echo "true" || echo "false")
sw_rsync=$(_cmd rsync && echo "true" || echo "false")
sw_jq=$(   _cmd jq    && echo "true" || echo "false")

# ── поточний користувач ───────────────────────────────────────────────────────

usr_name=$(_try id -un)
usr_uid=$( _try id -u)
usr_groups=$(_try id -Gn | tr ' ' ',')

usr_sudo="none"
if _cmd sudo; then
    if _try sudo -n true; then
        usr_sudo="nopasswd"
    else
        usr_sudo="available"
    fi
fi

usr_in_docker=false
_try id -nG | grep -qw docker && usr_in_docker=true

# ── наявні сервіси та контейнери infrabox ────────────────────────────────────

existing_services_json="["
first_svc=1
if _cmd systemctl; then
    for svc in infrabox-core infrabox-ui infrabox-arch infrabox-adm infrabox-redis infrabox-mqtt; do
        status=$(_try systemctl is-active "$svc")
        [ -z "$status" ] || [ "$status" = "" ] && continue
        [ "$first_svc" = "1" ] && first_svc=0 || existing_services_json+=","
        existing_services_json+="{\"service\":\"$svc\",\"status\":\"$(_esc "$status")\"}"
    done
fi
existing_services_json+="]"

existing_containers_json="["
first_ctr=1
if _cmd docker && docker info &>/dev/null 2>&1; then
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        name=$(echo "$line" | awk '{print $1}')
        status=$(echo "$line" | awk '{$1=""; print $0}' | xargs)
        [ "$first_ctr" = "1" ] && first_ctr=0 || existing_containers_json+=","
        existing_containers_json+="{\"name\":\"$(_esc "$name")\",\"status\":\"$(_esc "$status")\"}"
    done < <(_try docker ps -a --format "{{.Names}} {{.Status}}" | grep -i infrabox)
fi
existing_containers_json+="]"

# ── збірка JSON ───────────────────────────────────────────────────────────────

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

JSON=$(cat <<ENDJSON
{
  "probe_version": "1",
  "timestamp": "$TIMESTAMP",
  "hardware": {
    "arch":          "$(_esc "${hw_arch:-}")",
    "cpu_count":     ${hw_cpu_count:-0},
    "cpu_model":     "$(_esc "${hw_cpu_model:-}")",
    "ram_mb":        ${hw_ram_mb:-0},
    "disk_free_gb":  ${hw_disk_free_gb:-0},
    "board":         "$(_esc "${hw_board:-}")"
  },
  "os": {
    "platform":         "$PLATFORM",
    "id":               "$(_esc "${os_id:-}")",
    "version":          "$(_esc "${os_version:-}")",
    "pretty":           "$(_esc "${os_pretty:-}")",
    "kernel":           "$(_esc "${os_kernel:-}")",
    "bits":             ${os_bits:-64},
    "systemd_version":  "$(_esc "${os_systemd:-}")"
  },
  "network": {
    "hostname":   "$(_esc "${net_hostname:-}")",
    "fqdn":       "$(_esc "${net_fqdn:-}")",
    "interfaces": $net_ifaces_json,
    "gateway":    "$(_esc "${net_gateway:-}")"
  },
  "ports": $ports_json,
  "software": {
    "docker":         "$(_esc "${sw_docker:-}")",
    "docker_compose": "$(_esc "${sw_compose:-}")",
    "python3":        "$(_esc "${sw_python:-}")",
    "git":            "$(_esc "${sw_git:-}")",
    "curl":           $sw_curl,
    "wget":           $sw_wget,
    "rsync":          $sw_rsync,
    "jq":             $sw_jq
  },
  "user": {
    "name":           "$(_esc "${usr_name:-}")",
    "uid":            ${usr_uid:-0},
    "groups":         "$(_esc "${usr_groups:-}")",
    "sudo":           "$(_esc "${usr_sudo:-}")",
    "in_docker_group": $usr_in_docker
  },
  "infrabox_existing": {
    "services":   $existing_services_json,
    "containers": $existing_containers_json
  }
}
ENDJSON
)

# ── вивід ─────────────────────────────────────────────────────────────────────

if [ "$JSON_ONLY" = "1" ]; then
    echo "$JSON"
else
    echo "$JSON" | tee "$OUTPUT_FILE"
    echo "" >&2
    echo "✓ Збережено: $(pwd)/$OUTPUT_FILE" >&2
fi
