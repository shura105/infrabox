#!/usr/bin/env python3
# _topo.py — спільний парсер topology.yml для всіх admin-скриптів Infrabox.
#
# Єдине місце правди про схему topology.yml. Замінює потрійне inline-дублювання
# в deploy.sh / status.sh / uninstall.sh.
#
# Usage:
#   python3 _topo.py <topology.yml> <command> [args...]
#
# Commands (друкують або JSON, або shell-eval рядки, або списки по рядку):
#   json                  → повний topology як JSON (indent=2)
#   system                → eval: SYS_NAME SYS_DESC SYS_TZ SYS_REPO SYS_BRANCH
#   nodes                 → список node alias (по рядку)
#   node <alias>          → eval: NODE_ALIAS NODE_HOST NODE_USER NODE_KEY
#                                 NODE_DEPLOY_DIR NODE_ARCH NODE_OS NODE_ROLE
#   subs                  → список subsystem id (по рядку, у порядку deploy_order)
#   sub <id>              → eval: SUB_NODE SUB_WORKDIR
#   sub-containers <id>   → контейнери підсистеми (по рядку)
#   order deploy|undeploy → порядок (по рядку)
#   nodes-for <id...>     → унікальні вузли для набору підсистем (у порядку появи)
#   subs-on <alias>       → підсистеми, призначені на вузол (по рядку, у deploy_order)
#   ports <alias>         → host-порти, що їх expose підсистеми цього вузла (по рядку)
#   data-binds            → рядки "path|||description|||critical(0/1)"
#   data-vols             → рядки "name|||backup_cmd|||critical(0/1)"
#
# Залежність: лише стандартний python3 (без PyYAML).

import json
import re
import sys


# ── Парсинг ────────────────────────────────────────────────────────────────────
def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _unquote(s):
    s = s.strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 0 else s[1:]
    if s.startswith("'"):
        end = s.find("'", 1)
        return s[1:end] if end > 0 else s[1:]
    # зняти inline YAML-коментар у незакавиченому значенні
    if "  #" in s:
        s = s[: s.index("  #")].strip()
    elif " #" in s:
        s = s[: s.index(" #")].strip()
    return s


def _coerce(v):
    """Незакавичене значення → bool якщо схоже, інакше рядок."""
    if isinstance(v, bool):
        return v
    sl = v.lower()
    if sl in ("true", "yes"):
        return True
    if sl in ("false", "no"):
        return False
    return v


def parse(path):
    with open(path) as f:
        raw = f.read()

    data = {
        "system": {},
        "nodes": {},
        "subsystems": {},
        "deploy_order": [],
        "undeploy_order": [],
        "data": [],
    }

    ctx0 = None       # секція верхнього рівня
    ctx1 = None       # alias вузла / id підсистеми
    ctx2 = None       # підключ-список усередині підсистеми (containers / ports_exposed)
    cur_data = None   # поточний елемент списку data

    for line in raw.split("\n"):
        s = line.rstrip()
        if not s or s.lstrip().startswith("#"):
            continue

        lvl = _indent(s)
        text = s.strip()

        # ── рівень 0: секції ──
        if lvl == 0:
            ctx0 = text.rstrip(":").strip()
            ctx1 = ctx2 = None
            cur_data = None
            continue

        # ── рівень 2: підключі секцій / елементи списків ──
        if lvl == 2:
            ctx2 = None
            if text.startswith("- "):
                rest = text[2:].strip()
                if ctx0 == "deploy_order":
                    data["deploy_order"].append(rest.strip("\"'"))
                elif ctx0 == "undeploy_order":
                    data["undeploy_order"].append(rest.strip("\"'"))
                elif ctx0 == "data":
                    cur_data = {}
                    data["data"].append(cur_data)
                    if ":" in rest:
                        k, _, v = rest.partition(":")
                        cur_data[k.strip()] = _coerce(_unquote(v))
            elif ":" in text:
                k, _, v = text.partition(":")
                k = k.strip()
                v = _unquote(v)
                if ctx0 == "system":
                    data["system"][k] = v
                elif ctx0 in ("nodes", "subsystems"):
                    ctx1 = k
                    data[ctx0][k] = {}
            continue

        # ── рівень 4: поля вузла / підсистеми / елемента data ──
        if lvl == 4:
            # елементи списку containers (- infrabox-x)
            if text.startswith("- ") and ctx0 == "subsystems" and ctx1 and ctx2 == "containers":
                data["subsystems"][ctx1].setdefault("containers", []).append(
                    text[2:].strip().strip("\"'")
                )
                continue
            # елементи списку ports_exposed (- { host: N, ... })
            if text.startswith("- ") and ctx0 == "subsystems" and ctx1 and ctx2 == "ports_exposed":
                item = _parse_inline_map(text[2:].strip())
                if item:
                    data["subsystems"][ctx1].setdefault("ports_exposed", []).append(item)
                continue
            if ":" not in text:
                continue
            k, _, v = text.partition(":")
            k = k.strip()
            vs = v.strip()
            # підключ-список усередині підсистеми
            if ctx0 == "subsystems" and ctx1 and k in ("containers", "ports_exposed"):
                ctx2 = k
                # inline-форма "ports_exposed: []" → порожньо
                if vs in ("[]", ""):
                    data["subsystems"][ctx1].setdefault(k, [])
                continue
            ctx2 = None
            val = _unquote(v)
            if ctx0 == "nodes" and ctx1:
                data["nodes"][ctx1][k] = val
            elif ctx0 == "subsystems" and ctx1:
                data["subsystems"][ctx1][k] = val
            elif ctx0 == "data" and cur_data is not None:
                cur_data[k] = _coerce(val)
            continue

        # ── рівень 6: елементи списків усередині підсистеми ──
        if lvl >= 6 and ctx0 == "subsystems" and ctx1:
            if text.startswith("- ") and ctx2 == "containers":
                data["subsystems"][ctx1].setdefault("containers", []).append(
                    text[2:].strip().strip("\"'")
                )
            elif text.startswith("- ") and ctx2 == "ports_exposed":
                item = _parse_inline_map(text[2:].strip())
                if item:
                    data["subsystems"][ctx1].setdefault("ports_exposed", []).append(item)
            continue

    return data


def _parse_inline_map(s):
    """{ host: 1883, container: 1883, service: "x", proto: "mqtt" } → dict."""
    s = s.strip()
    if s.startswith("{"):
        s = s[1:]
    if s.endswith("}"):
        s = s[:-1]
    out = {}
    # розбити по комах, що не всередині лапок
    for part in re.split(r",(?=(?:[^\"']*[\"'][^\"']*[\"'])*[^\"']*$)", s):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, _, v = part.partition(":")
        out[k.strip()] = _unquote(v)
    return out


# ── Вивід ──────────────────────────────────────────────────────────────────────
def _q(v):
    return json.dumps(str(v), ensure_ascii=False)


def _err(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


def cmd_json(d, args):
    print(json.dumps(d, ensure_ascii=False, indent=2))


def cmd_system(d, args):
    s = d["system"]
    print(f"SYS_NAME={_q(s.get('name', 'Infrabox'))}")
    print(f"SYS_DESC={_q(s.get('description', ''))}")
    print(f"SYS_TZ={_q(s.get('timezone', 'Europe/Kyiv'))}")
    print(f"SYS_REPO={_q(s.get('repo', ''))}")
    print(f"SYS_BRANCH={_q(s.get('branch', 'main'))}")


def cmd_nodes(d, args):
    for alias in d["nodes"]:
        print(alias)


def cmd_node(d, args):
    if not args:
        _err("node: потрібен alias")
    alias = args[0]
    n = d["nodes"].get(alias)
    if n is None:
        _err(f"node: вузол {alias!r} не знайдено")
    print(f"NODE_ALIAS={_q(alias)}")
    print(f"NODE_HOST={_q(n.get('host', ''))}")
    print(f"NODE_USER={_q(n.get('user', ''))}")
    print(f"NODE_KEY={_q(n.get('ssh_key', '~/.ssh/id_ed25519'))}")
    print(f"NODE_DEPLOY_DIR={_q(n.get('deploy_dir', ''))}")
    print(f"NODE_ARCH={_q(n.get('arch', ''))}")
    print(f"NODE_OS={_q(n.get('os', ''))}")
    print(f"NODE_ROLE={_q(n.get('role', ''))}")


def _ordered_subs(d):
    """Підсистеми у порядку deploy_order; решта (не згадані) — у кінці."""
    order = d.get("deploy_order", [])
    subs = list(d["subsystems"].keys())
    seen = []
    for s in order:
        if s in d["subsystems"] and s not in seen:
            seen.append(s)
    for s in subs:
        if s not in seen:
            seen.append(s)
    return seen


def cmd_subs(d, args):
    for s in _ordered_subs(d):
        print(s)


def cmd_sub(d, args):
    if not args:
        _err("sub: потрібен id")
    sid = args[0]
    s = d["subsystems"].get(sid)
    if s is None:
        _err(f"sub: підсистему {sid!r} не знайдено")
    print(f"SUB_NODE={_q(s.get('node', ''))}")
    print(f"SUB_WORKDIR={_q(s.get('workdir', sid))}")


def cmd_sub_containers(d, args):
    if not args:
        _err("sub-containers: потрібен id")
    sid = args[0]
    s = d["subsystems"].get(sid, {})
    for c in s.get("containers", []):
        print(c)


def cmd_order(d, args):
    which = args[0] if args else "deploy"
    if which == "undeploy":
        order = d.get("undeploy_order") or list(reversed(d.get("deploy_order", [])))
    else:
        order = d.get("deploy_order", [])
    for s in order:
        print(s)


def cmd_nodes_for(d, args):
    """Унікальні вузли для заданого набору підсистем, у порядку появи."""
    seen = []
    for sid in args:
        s = d["subsystems"].get(sid)
        if not s:
            continue
        node = s.get("node", "")
        if node and node not in seen:
            seen.append(node)
    for n in seen:
        print(n)


def cmd_subs_on(d, args):
    """Підсистеми, призначені на вузол, у порядку deploy_order."""
    if not args:
        _err("subs-on: потрібен alias")
    alias = args[0]
    for sid in _ordered_subs(d):
        if d["subsystems"][sid].get("node", "") == alias:
            print(sid)


def cmd_ports(d, args):
    """Host-порти, що їх expose підсистеми на заданому вузлі."""
    if not args:
        _err("ports: потрібен alias")
    alias = args[0]
    seen = []
    for sid, s in d["subsystems"].items():
        if s.get("node", "") != alias:
            continue
        for p in s.get("ports_exposed", []):
            host_port = p.get("host", "")
            if host_port and host_port not in seen:
                seen.append(host_port)
    for p in seen:
        print(p)


def cmd_data_binds(d, args):
    for item in d["data"]:
        if item.get("type") == "bind":
            path = item.get("path", "")
            desc = item.get("description", "")
            crit = "1" if item.get("critical") else "0"
            print(f"{path}|||{desc}|||{crit}")


def cmd_data_vols(d, args):
    for item in d["data"]:
        if item.get("type") == "docker_volume":
            name = item.get("name", "")
            backup = item.get("backup_cmd", "")
            crit = "1" if item.get("critical") else "0"
            print(f"{name}|||{backup}|||{crit}")


COMMANDS = {
    "json": cmd_json,
    "system": cmd_system,
    "nodes": cmd_nodes,
    "node": cmd_node,
    "subs": cmd_subs,
    "sub": cmd_sub,
    "sub-containers": cmd_sub_containers,
    "order": cmd_order,
    "nodes-for": cmd_nodes_for,
    "subs-on": cmd_subs_on,
    "ports": cmd_ports,
    "data-binds": cmd_data_binds,
    "data-vols": cmd_data_vols,
}


def main(argv):
    if len(argv) < 3:
        _err("Usage: _topo.py <topology.yml> <command> [args...]")
    path, command, args = argv[1], argv[2], argv[3:]
    if command not in COMMANDS:
        _err(f"Невідома команда: {command}\nДоступні: {', '.join(COMMANDS)}")
    try:
        d = parse(path)
    except FileNotFoundError:
        _err(f"Не знайдено: {path}")
    if not d["nodes"]:
        _err("ERROR: nodes не знайдено в topology.yml")
    COMMANDS[command](d, args)


if __name__ == "__main__":
    main(sys.argv)
