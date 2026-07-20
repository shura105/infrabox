import contextlib
import json
import os
import pathlib
import shlex
import socket
import subprocess
import tempfile
import threading
import time

import docker as docker_sdk
import redis as redis_sync
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

REDIS_HOST = os.environ.get("REDIS_HOST", "infrabox-redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

# shared sync Redis client (uses connection pool internally) — avoids creating
# a fresh socket on every request
_redis = redis_sync.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


# containers that cannot write their own heartbeat (no Python runtime)
_PROXY_CONTAINERS = [
    "infrabox-redis",
    "infrabox-mosquitto-real",
    "infrabox-mosquitto-sim",
    "infrabox-web",
    "infrabox-arch-ui",
    "portainer",
]


def _heartbeat_thread():
    r = None
    docker_client = None
    _docker_tick = 0
    while True:
        try:
            if r is None:
                r = redis_sync.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            ts = int(time.time())
            r.set("heartbeat:infrabox-adm", ts, ex=25)
            # Docker API is polled every 3s — cheaper than every tick, still within TTL=5s
            _docker_tick += 1
            if _docker_tick >= 3:
                _docker_tick = 0
                try:
                    if docker_client is None:
                        docker_client = docker_sdk.from_env()
                    running = {c.name for c in docker_client.containers.list(
                        filters={"name": _PROXY_CONTAINERS}
                    ) if c.status == "running"}
                    pipe = r.pipeline()
                    for name in _PROXY_CONTAINERS:
                        if name in running:
                            pipe.set(f"heartbeat:{name}", ts, ex=25)
                    pipe.execute()
                except Exception:
                    docker_client = None
        except Exception:
            r = None
        time.sleep(5)


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

SELF        = os.environ.get("HOSTNAME", "infrabox-adm")
CONFIG_FILE = pathlib.Path("/app/config/infrabox.json")


# ── infrabox.json ──────────────────────────────────────────────────────────────
def _read_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def _write_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


# ── SSH helpers ────────────────────────────────────────────────────────────────
def _get_host(host_id: str) -> dict:
    cfg = _read_config()
    host = next((h for h in cfg.get("hosts", []) if h["id"] == host_id), None)
    if not host:
        raise HTTPException(404, f"Host {host_id!r} not found")
    return host


def _get_sub_with_host(sub_id: str) -> tuple[dict, dict]:
    cfg = _read_config()
    s = next((x for x in cfg.get("subsystems", []) if x["id"] == sub_id), None)
    if not s:
        raise HTTPException(404, f"Subsystem {sub_id!r} not found")
    host = next((h for h in cfg.get("hosts", []) if h["id"] == s.get("host")), None)
    if not host:
        raise HTTPException(404, f"Host for subsystem {sub_id!r} not found")
    return s, host


def _ssh_base(host: dict, key_file: str) -> list[str]:
    user = host.get("user", "root")
    addr = host.get("addr", host["id"])
    return [
        "ssh",
        "-i", key_file,
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        f"{user}@{addr}",
    ]


@contextlib.contextmanager
def _ssh_key_ctx(host: dict):
    key_str = host.get("ssh_key", "").strip()
    if not key_str:
        raise HTTPException(400, f"SSH key not configured for host {host['id']!r}")
    fd, path = tempfile.mkstemp(suffix=".key", dir="/tmp")
    try:
        os.write(fd, (key_str + "\n").encode())
        os.close(fd)
        os.chmod(path, 0o600)
        yield path
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def _ssh_run(host: dict, cmd: str, timeout: int = 300) -> dict:
    with _ssh_key_ctx(host) as key_file:
        r = subprocess.run(
            _ssh_base(host, key_file) + [cmd],
            capture_output=True, text=True, timeout=timeout,
        )
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or r.stdout or "SSH error")[-2000:])
    return {"ok": True, "out": (r.stdout or "")[-1000:]}


def _compose_ssh(workdir: str) -> str:
    """Shell snippet: cd to workdir + docker compose v2."""
    wd = shlex.quote(workdir)
    return f"cd {wd} && docker compose"


# ── Docker SDK (local socket — status reads only) ─────────────────────────────
def _docker():
    return docker_sdk.from_env()


def _get_container(name: str):
    try:
        return _docker().containers.get(name)
    except docker_sdk.errors.NotFound:
        raise HTTPException(404, f"Container {name!r} not found")
    except Exception as e:
        raise HTTPException(500, str(e))


def _safe_status(c):
    try:
        c.reload()
    except Exception:
        pass
    return c.status


def _sub_status(workdir: str) -> dict:
    try:
        all_c   = _docker().containers.list(all=True)
        mine    = [c for c in all_c
                   if (c.labels or {}).get("com.docker.compose.project.working_dir", "") == workdir]
        total   = len(mine)
        running = sum(1 for c in mine if c.status == "running")
        exited  = sum(1 for c in mine if c.status in ("exited", "created"))
        return {"total": total, "running": running, "exited": exited}
    except Exception:
        return {"total": 0, "running": 0, "exited": 0}


# ── config endpoints ───────────────────────────────────────────────────────────
@app.get("/config")
def get_config():
    cfg = _read_config()
    if not cfg:
        raise HTTPException(404, "infrabox.json not found or invalid")
    return cfg


class SshKeyIn(BaseModel):
    key: str
    user: str = ""


@app.patch("/config/hosts/{host_id}/ssh_key")
def set_ssh_key(host_id: str, body: SshKeyIn):
    cfg = _read_config()
    if not cfg:
        raise HTTPException(404, "infrabox.json not found")
    host = next((h for h in cfg.get("hosts", []) if h["id"] == host_id), None)
    if not host:
        raise HTTPException(404, f"Host {host_id!r} not found")
    host["ssh_key"] = body.key.strip()
    if body.user.strip():
        host["user"] = body.user.strip()
    _write_config(cfg)
    return {"ok": True}


class SshTestIn(BaseModel):
    key: str
    user: str


@app.post("/config/hosts/{host_id}/ssh_test")
def test_ssh_key(host_id: str, body: SshTestIn):
    cfg = _read_config()
    if not cfg:
        raise HTTPException(404, "infrabox.json not found")
    host = next((h for h in cfg.get("hosts", []) if h["id"] == host_id), None)
    if not host:
        raise HTTPException(404, f"Host {host_id!r} not found")
    addr    = host.get("addr", host_id)
    user    = body.user.strip() or host.get("user", "root")
    key_str = body.key.strip()
    if not key_str:
        raise HTTPException(400, "Ключ порожній")
    key_file = pathlib.Path(f"/tmp/ssh_test_{host_id}.key")
    try:
        key_file.write_text(key_str + "\n")
        key_file.chmod(0o600)
        r = subprocess.run(
            ["ssh", "-i", str(key_file),
             "-o", "StrictHostKeyChecking=no",
             "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=8",
             f"{user}@{addr}", "echo infrabox-ok"],
            capture_output=True, text=True, timeout=12,
        )
        if r.returncode == 0 and "infrabox-ok" in r.stdout:
            return {"ok": True, "msg": f"{user}@{addr}"}
        err = (r.stderr or r.stdout or "невідома помилка").strip().splitlines()[-1]
        raise HTTPException(500, err)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        key_file.unlink(missing_ok=True)


# ── subsystems ─────────────────────────────────────────────────────────────────
@app.get("/subsystems")
def list_subsystems():
    cfg = _read_config()
    return [
        {"id": s["id"], "name": s["name"], "workdir": s["workdir"], **_sub_status(s["workdir"])}
        for s in cfg.get("subsystems", [])
    ]


@app.post("/subsystems/{sub_id}/start")
def sub_start(sub_id: str):
    s, host = _get_sub_with_host(sub_id)
    return _ssh_run(host, f"{_compose_ssh(s['workdir'])} up -d")


@app.post("/subsystems/{sub_id}/stop")
def sub_stop(sub_id: str):
    s, host = _get_sub_with_host(sub_id)
    return _ssh_run(host, f"{_compose_ssh(s['workdir'])} stop")


@app.post("/subsystems/{sub_id}/restart")
def sub_restart(sub_id: str):
    s, host = _get_sub_with_host(sub_id)
    wd = shlex.quote(s["workdir"])
    # detached so SSH returns immediately even if this container is part of the subsystem
    return _ssh_run(host, f"nohup sh -c 'cd {wd} && docker compose restart' >/dev/null 2>&1 &")


@app.post("/subsystems/{sub_id}/down")
def sub_down(sub_id: str):
    s, host = _get_sub_with_host(sub_id)
    return _ssh_run(host, f"{_compose_ssh(s['workdir'])} down")


@app.post("/subsystems/{sub_id}/build/stream")
def sub_build_stream(sub_id: str):
    s, host = _get_sub_with_host(sub_id)
    workdir = s["workdir"]
    key_str = host.get("ssh_key", "").strip()

    def generate():
        if not key_str:
            yield "[ERROR: SSH key not configured for this host]\n[EXIT 1]\n"
            return

        fd, key_path = tempfile.mkstemp(suffix=".key", dir="/tmp")
        try:
            os.write(fd, (key_str + "\n").encode())
            os.close(fd)
            os.chmod(key_path, 0o600)
            user = host.get("user", "root")
            addr = host.get("addr", host["id"])
            # -tt forces pseudo-TTY on remote → line-buffered output, no 4KB delay
            ssh = ["ssh", "-tt", "-i", key_path,
                   "-o", "StrictHostKeyChecking=no",
                   "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=10",
                   f"{user}@{addr}"]
            wd = shlex.quote(workdir)

            for cmd in [
                f"cd {wd} && docker compose build --no-cache",
                f"cd {wd} && docker compose up -d",
            ]:
                proc = subprocess.Popen(
                    ssh + [cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for line in proc.stdout:
                    yield line.replace("\r\n", "\n").replace("\r", "\n")
                proc.wait()
                if proc.returncode != 0:
                    yield f"\n[EXIT {proc.returncode}]\n"
                    return

            yield "\n[EXIT 0]\n"
        finally:
            try:
                os.unlink(key_path)
            except Exception:
                pass

    return StreamingResponse(generate(), media_type="text/plain")


# ── containers list ────────────────────────────────────────────────────────────
@app.get("/containers")
def list_containers():
    try:
        containers = _docker().containers.list(all=True)
    except Exception as e:
        raise HTTPException(500, str(e))
    result = []
    for c in containers:
        lbl  = c.labels or {}
        tags = c.image.tags
        result.append({
            "id":      c.short_id,
            "name":    c.name,
            "status":  c.status,
            "image":   tags[0] if tags else c.image.short_id,
            "project": lbl.get("com.docker.compose.project", ""),
            "service": lbl.get("com.docker.compose.service", ""),
            "workdir": lbl.get("com.docker.compose.project.working_dir", ""),
        })
    return sorted(result, key=lambda x: x["name"])


# ── single container actions ───────────────────────────────────────────────────
@app.post("/containers/{name}/start")
def start(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get_container(name)
    try:
        if c.status == "paused":
            c.unpause()
        else:
            c.start()
        return {"ok": True, "status": _safe_status(c)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/containers/{name}/stop")
def stop(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get_container(name)
    try:
        c.stop(timeout=10)
        return {"ok": True, "status": _safe_status(c)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/containers/{name}/restart")
def restart(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get_container(name)
    try:
        c.restart(timeout=10)
        return {"ok": True, "status": _safe_status(c)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/containers/{name}")
def remove(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get_container(name)
    try:
        c.remove(force=True)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/containers/{name}/rebuild")
def rebuild(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c   = _get_container(name)
    lbl = c.labels or {}
    workdir = lbl.get("com.docker.compose.project.working_dir", "")
    service = lbl.get("com.docker.compose.service", "")
    if not workdir or not service:
        raise HTTPException(400, "Container is not managed by docker compose")
    cfg = _read_config()
    sub = next((s for s in cfg.get("subsystems", []) if s.get("workdir") == workdir), None)
    if not sub:
        raise HTTPException(400, "Cannot determine host for this container")
    host = next((h for h in cfg.get("hosts", []) if h["id"] == sub.get("host")), None)
    if not host:
        raise HTTPException(400, "Host not found for this container")
    return _ssh_run(host, f"{_compose_ssh(workdir)} up -d {shlex.quote(service)}", timeout=600)


# ── host info & control ────────────────────────────────────────────────────────
def _local_hostname() -> str:
    try:
        return pathlib.Path("/host_hostname").read_text().strip() or socket.gethostname()
    except Exception:
        return socket.gethostname()


def _local_host() -> dict | None:
    """Find the infrabox.json host entry that matches this machine."""
    name = _local_hostname()
    cfg  = _read_config()
    return next(
        (h for h in cfg.get("hosts", [])
         if name in (h.get("addr", ""), h.get("name", ""), h.get("id", ""))),
        None,
    )


@app.get("/host/status")
def host_status():
    try:
        uptime_s   = float(pathlib.Path("/proc/uptime").read_text().split()[0])
        load_raw   = pathlib.Path("/proc/loadavg").read_text().split()
        days       = int(uptime_s // 86400)
        hours      = int((uptime_s % 86400) // 3600)
        minutes    = int((uptime_s % 3600) // 60)
        uptime_str = (f"{days}д " if days else "") + f"{hours:02d}:{minutes:02d}"
        return {
            "hostname": _local_hostname(),
            "uptime":   uptime_str,
            "uptime_s": uptime_s,
            "load":     f"{load_raw[0]} {load_raw[1]} {load_raw[2]}",
        }
    except Exception as e:
        raise HTTPException(500, str(e))


def _power_cmd(cmd: str):
    """Run power command via SSH (sudo). Fallback: nsenter into host PID namespace."""
    host = _local_host()
    if host and host.get("ssh_key"):
        try:
            _ssh_run(host, f"sudo {cmd}", timeout=10)
            return {"ok": True}
        except Exception:
            pass
    # fallback: nsenter into PID 1 namespace (requires pid:host in compose)
    try:
        subprocess.Popen(
            ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid",
             "--"] + cmd.split()
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/host/reboot")
def host_reboot():
    return _power_cmd("shutdown -r now")


@app.post("/host/shutdown")
def host_shutdown():
    return _power_cmd("shutdown -h now")


# ─────────────────────────────────────────────────────────────────────────────
# Points management
# ─────────────────────────────────────────────────────────────────────────────
POINTS_PATH   = "/app/config/points.json"
OBJECTS_PATH  = "/app/config/objects.json"
DROPS_PATH    = "/app/config/drops.json"
SYSTEMS_PATH  = "/app/config/systems.json"
SOCKETS_PATH  = "/app/config/sockets.json"
BACKUP_DIR    = pathlib.Path("/app/config/backup")

BACKUP_FILES  = {
    "points.json":          POINTS_PATH,
    "objects.json":         OBJECTS_PATH,
    "drops.json":           DROPS_PATH,
    "systems.json":         SYSTEMS_PATH,
    "sys_params.json":      "/app/config/sys_params.json",
    "serv_subsystems.json": "/app/config/serv_subsystems.json",
}


@app.post("/config/backup")
def create_backup():
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / ts
    dest.mkdir(parents=True, exist_ok=True)
    for fname, src in BACKUP_FILES.items():
        try:
            import shutil
            shutil.copy2(src, dest / fname)
        except Exception as e:
            raise HTTPException(500, f"Failed to backup {fname}: {e}")
    return {"ok": True, "backup": ts}


@app.get("/config/sockets")
def get_sockets():
    with open(SOCKETS_PATH) as f:
        return json.load(f)


def _read_points():
    with open(POINTS_PATH) as f:
        return json.load(f)


def _write_points(points):
    with open(POINTS_PATH, "w") as f:
        json.dump(points, f, ensure_ascii=False, indent=2)


@app.get("/objects")
def get_objects():
    with open(OBJECTS_PATH) as f:
        return json.load(f)

@app.get("/drops")
def get_drops():
    with open(DROPS_PATH) as f:
        return json.load(f)

@app.get("/systems")
def get_systems():
    with open(SYSTEMS_PATH) as f:
        return json.load(f)

SYS_PARAMS_PATH     = "/app/config/sys_params.json"
SUBSYSTEMS_CFG_PATH = "/app/config/serv_subsystems.json"


@app.get("/sys-params")
def get_sys_params():
    with open(SYS_PARAMS_PATH) as f:
        return json.load(f)


@app.patch("/sys-params/system")
def patch_sys_params_system(body: dict):
    """Оновити окремі поля sys_params['system']. Повертає оновлений system."""
    ALLOWED = {"arch_ram_budget_mb", "log_level", "system_tick_ms"}
    unknown = set(body) - ALLOWED
    if unknown:
        raise HTTPException(400, f"Недозволені поля: {unknown}")

    def m(data):
        for k, v in body.items():
            data["system"][k] = v
        return data["system"]

    return _rw_json(SYS_PARAMS_PATH, m)


@app.put("/sys-params")
def put_sys_params(body: dict):
    """Replace the whole sys_params.json. bootstrap changes (data_source, redis,
    mqtt) require a core restart to take effect — use «Застосувати зміни» after."""
    if not isinstance(body, dict) or "bootstrap" not in body or "system" not in body:
        raise HTTPException(400, "Очікується об'єкт із секціями 'bootstrap' і 'system'")
    ds = body.get("bootstrap", {}).get("data_source")
    if ds not in ("real", "sim"):
        raise HTTPException(400, "bootstrap.data_source має бути 'real' або 'sim'")
    def m(data):
        keep = data.get("schema_version", 1)
        data.clear()
        data.update(body)
        data.setdefault("schema_version", keep)
        return data
    return _rw_json(SYS_PARAMS_PATH, m)


# ── Objects CRUD ───────────────────────────────────────────────────────────────
class ObjectIn(BaseModel):
    id: str
    name: str


def _rw_json(path: str, mutate):
    with open(path) as f:
        data = json.load(f)
    result = mutate(data)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return result


@app.post("/objects")
def create_object(body: ObjectIn):
    def m(items):
        if any(x["id"] == body.id for x in items):
            raise HTTPException(409, f"Object {body.id!r} already exists")
        items.append(body.model_dump())
    _rw_json(OBJECTS_PATH, m)
    return {"ok": True}


@app.put("/objects/{obj_id}")
def update_object(obj_id: str, body: ObjectIn):
    def m(items):
        for i, x in enumerate(items):
            if x["id"] == obj_id:
                items[i] = body.model_dump()
                return
        raise HTTPException(404, f"Object {obj_id!r} not found")
    _rw_json(OBJECTS_PATH, m)
    if body.id != obj_id:   # cascade rename to dependents
        _rw_json(DROPS_PATH,  _rename_field_mut("object", obj_id, body.id))
        _rw_json(POINTS_PATH, _rename_field_mut("object", obj_id, body.id))
    return {"ok": True}


@app.delete("/objects/{obj_id}")
def delete_object(obj_id: str):
    def m(items):
        new = [x for x in items if x["id"] != obj_id]
        if len(new) == len(items):
            raise HTTPException(404, f"Object {obj_id!r} not found")
        items[:] = new
    _rw_json(OBJECTS_PATH, m)
    with open(DROPS_PATH) as f:
        gone_drops = {d["id"] for d in json.load(f) if d.get("object") == obj_id}
    removed = [p["id"] for p in _read_points() if p.get("object") == obj_id]
    _rw_json(DROPS_PATH,   lambda d: d.__setitem__(slice(None), [x for x in d if x.get("object") != obj_id]))
    _rw_json(SYSTEMS_PATH, lambda d: d.__setitem__(slice(None), [x for x in d if x.get("drop") not in gone_drops]))
    _rw_json(POINTS_PATH,  lambda d: d.__setitem__(slice(None), [x for x in d if x.get("object") != obj_id]))
    _rw_json(SUBSYSTEMS_CFG_PATH, _purge_node_infra_mut(gone_drops))
    _rw_json(SOCKETS_PATH,        _purge_node_infra_mut(gone_drops))
    _redis_purge_ids(removed)
    return {"ok": True}


# ── Systems CRUD ───────────────────────────────────────────────────────────────
class SystemIn(BaseModel):
    id: str
    name: str
    drop: str   # was "object" — now system belongs to a node/drop
    operation_mode: str = "manual"


@app.post("/systems")
def create_system(body: SystemIn):
    def m(items):
        if any(x["id"] == body.id for x in items):
            raise HTTPException(409, f"System {body.id!r} already exists")
        items.append(body.model_dump())
    _rw_json(SYSTEMS_PATH, m)
    return {"ok": True}


@app.put("/systems/{sys_id}")
def update_system(sys_id: str, body: SystemIn):
    def m(items):
        for i, x in enumerate(items):
            if x["id"] == sys_id:
                items[i] = body.model_dump()
                return
        raise HTTPException(404, f"System {sys_id!r} not found")
    _rw_json(SYSTEMS_PATH, m)
    if body.id != sys_id:   # cascade rename to dependent points
        _rw_json(POINTS_PATH, _rename_field_mut("system", sys_id, body.id))
    return {"ok": True}


@app.delete("/systems/{sys_id}")
def delete_system(sys_id: str):
    def m(items):
        new = [x for x in items if x["id"] != sys_id]
        if len(new) == len(items):
            raise HTTPException(404, f"System {sys_id!r} not found")
        items[:] = new
    _rw_json(SYSTEMS_PATH, m)
    removed = [p["id"] for p in _read_points() if p.get("system") == sys_id]
    _rw_json(POINTS_PATH, lambda d: d.__setitem__(slice(None), [x for x in d if x.get("system") != sys_id]))
    _redis_purge_ids(removed)
    return {"ok": True}


# ── Drops CRUD ─────────────────────────────────────────────────────────────────
class DropIn(BaseModel):
    id: str
    name: str
    object: str


@app.post("/drops")
def create_drop(body: DropIn):
    def m(items):
        if any(x["id"] == body.id for x in items):
            raise HTTPException(409, f"Drop {body.id!r} already exists")
        items.append(body.model_dump())
    _rw_json(DROPS_PATH, m)
    _scaffold_node_infra(body.id)   # auto-create serv_subsystems + sockets for the node
    return {"ok": True}


@app.put("/drops/{drop_id}")
def update_drop(drop_id: str, body: DropIn):
    def m(items):
        for i, x in enumerate(items):
            if x["id"] == drop_id:
                items[i] = body.model_dump()
                return
        raise HTTPException(404, f"Drop {drop_id!r} not found")
    _rw_json(DROPS_PATH, m)
    if body.id != drop_id:   # cascade rename to every file that references the node
        for path in (SYSTEMS_PATH, POINTS_PATH, SUBSYSTEMS_CFG_PATH, SOCKETS_PATH):
            _rw_json(path, _rename_field_mut("drop", drop_id, body.id))
    return {"ok": True}


@app.delete("/drops/{drop_id}")
def delete_drop(drop_id: str):
    def m(items):
        new = [x for x in items if x["id"] != drop_id]
        if len(new) == len(items):
            raise HTTPException(404, f"Drop {drop_id!r} not found")
        items[:] = new
    _rw_json(DROPS_PATH, m)
    removed = [p["id"] for p in _read_points() if p.get("drop") == drop_id]
    _rw_json(SYSTEMS_PATH, lambda d: d.__setitem__(slice(None), [x for x in d if x.get("drop") != drop_id]))
    _rw_json(POINTS_PATH,  lambda d: d.__setitem__(slice(None), [x for x in d if x.get("drop") != drop_id]))
    _rw_json(SUBSYSTEMS_CFG_PATH, _purge_node_infra_mut(drop_id))
    _rw_json(SOCKETS_PATH,        _purge_node_infra_mut(drop_id))
    _redis_purge_ids(removed)
    return {"ok": True}


# ── Cascade helpers ──────────────────────────────────────────────────────────
def _rename_field_mut(field, old_id, new_id):
    """Mutator for _rw_json: rename <field> value old_id → new_id across a list."""
    def mut(items):
        for x in items:
            if isinstance(x, dict) and x.get(field) == old_id:
                x[field] = new_id
    return mut


# ── Node infra scaffold (serv_subsystems + sockets tied to node lifecycle) ────
_SELFDIAG_HW = {"id": "selfDiag", "name": "Selfdiagnostic",
                "params": ["cpu_load", "mem_used", "disk_space", "net_rx", "net_tx"]}


def _svc_label(cname):
    """infrabox-arch-backend → 'Arch Backend', portainer → 'Portainer'."""
    base = cname[len("infrabox-"):] if cname.startswith("infrabox-") else cname
    return base.replace("-", " ").title()


def _infra_containers():
    """Running infrabox-* (+portainer) container names — the software topology."""
    names = []
    try:
        for c in _docker().containers.list():
            if c.name.startswith("infrabox-") or c.name == "portainer":
                names.append(c.name)
    except Exception:
        pass
    return sorted(names)


def _scaffold_subsystems(drop_id):
    subs = [{"id": n, "name": _svc_label(n)} for n in _infra_containers()]
    subs.append({"id": "selfDiag", "name": "Selfdiagnostic"})
    return {"drop": drop_id, "subsystems": subs}


def _scaffold_sockets(drop_id):
    software = [{"id": _svc_label(n), "name": n, "type": "heartbeat",
                 "params": ["heartbeat"]} for n in _infra_containers()]
    return {"drop": drop_id, "hardware": [dict(_SELFDIAG_HW)], "software": software}


def _scaffold_node_infra(drop_id):
    """On node create: add a serv_subsystems + sockets entry (if absent)."""
    def add_sub(items):
        if not any(n.get("drop") == drop_id for n in items):
            items.append(_scaffold_subsystems(drop_id))
    def add_sock(items):
        if not any(n.get("drop") == drop_id for n in items):
            items.append(_scaffold_sockets(drop_id))
    _rw_json(SUBSYSTEMS_CFG_PATH, add_sub)
    _rw_json(SOCKETS_PATH, add_sock)


def _purge_node_infra_mut(drop_ids):
    """Mutator: drop serv_subsystems/sockets node entries whose drop ∈ drop_ids."""
    ids = drop_ids if isinstance(drop_ids, set) else {drop_ids}
    return lambda d: d.__setitem__(slice(None), [n for n in d if n.get("drop") not in ids])


def _redis_purge_ids(ids):
    """Delete point:{id} Redis keys for the given ids (best-effort)."""
    keys = [f"point:{i}" for i in ids]
    if keys:
        with contextlib.suppress(Exception):
            _redis.delete(*keys)


def _impact(field, value):
    """Points in points.json whose <field> == value, plus which of them still
    have a live point:* key in Redis. Returns (affected_points, redis_ids)."""
    affected = [p for p in _read_points() if p.get(field) == value]
    ids = [p["id"] for p in affected]
    redis_ids = []
    if ids:
        with contextlib.suppress(Exception):
            pipe = _redis.pipeline()
            for i in ids:
                pipe.exists(f"point:{i}")
            redis_ids = [i for i, ex in zip(ids, pipe.execute()) if ex]
    return affected, redis_ids


def _impact_payload(affected, redis_ids):
    return {
        "points": [{"id": p["id"], "pointname": p.get("pointname", ""),
                    "type": p.get("type", "")} for p in affected],
        "redis_ids": redis_ids,
    }


@app.get("/objects/{obj_id}/impact")
def object_impact(obj_id: str):
    with open(DROPS_PATH) as f:
        drops = [d["id"] for d in json.load(f) if d.get("object") == obj_id]
    drop_set = set(drops)
    with open(SYSTEMS_PATH) as f:
        systems = [s["id"] for s in json.load(f) if s.get("drop") in drop_set]
    affected, redis_ids = _impact("object", obj_id)
    return {"drops": drops, "systems": systems, **_impact_payload(affected, redis_ids)}


@app.get("/systems/{sys_id}/impact")
def system_impact(sys_id: str):
    affected, redis_ids = _impact("system", sys_id)
    return _impact_payload(affected, redis_ids)


@app.get("/drops/{drop_id}/impact")
def drop_impact(drop_id: str):
    with open(SYSTEMS_PATH) as f:
        systems = [s["id"] for s in json.load(f) if s.get("drop") == drop_id]
    affected, redis_ids = _impact("drop", drop_id)
    return {"systems": systems, **_impact_payload(affected, redis_ids)}


@app.get("/config/integrity")
def config_integrity():
    """Cross-file referential check — dangling parent references across config."""
    def _load(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return []
    objects = _load(OBJECTS_PATH)
    drops   = _load(DROPS_PATH)
    systems = _load(SYSTEMS_PATH)
    points  = _read_points()
    subs    = _load(SUBSYSTEMS_CFG_PATH)
    socks   = _load(SOCKETS_PATH)

    obj_ids  = {o.get("id") for o in objects}
    drop_ids = {d.get("id") for d in drops}
    sys_ids  = {s.get("id") for s in systems}
    sock_by_drop = {}
    all_sock_ids = set()
    for n in socks:
        ids = set()
        for grp in ("hardware", "software"):
            for s in (n.get(grp) or []):
                if s.get("id"):
                    ids.add(s["id"])
        sock_by_drop[n.get("drop")] = ids
        all_sock_ids |= ids

    issues = []
    def add(kind, entity, field, value, detail):
        issues.append({"kind": kind, "entity": entity, "field": field,
                       "value": value, "detail": detail})

    for d in drops:
        if d.get("object") and d["object"] not in obj_ids:
            add("drop", d.get("id"), "object", d["object"], "вузол → неіснуючий об'єкт")
    for s in systems:
        if s.get("drop") and s["drop"] not in drop_ids:
            add("system", s.get("id"), "drop", s["drop"], "система → неіснуючий вузол")
    for p in points:
        pid = p.get("id")
        if p.get("object") and p["object"] not in obj_ids:
            add("point", pid, "object", p["object"], "параметр → неіснуючий об'єкт")
        if p.get("drop") and p["drop"] not in drop_ids:
            add("point", pid, "drop", p["drop"], "параметр → неіснуючий вузол")
        if p.get("system") and p["system"] not in sys_ids and p["system"] not in all_sock_ids:
            add("point", pid, "system", p["system"], "параметр → неіснуюча система/сокет")
        if p.get("socket") and p["socket"] not in sock_by_drop.get(p.get("drop"), set()):
            add("point", pid, "socket", p["socket"], "параметр → неіснуючий сокет на вузлі")
    for n in subs:
        if n.get("drop") and n["drop"] not in drop_ids:
            add("serv_subsystems", n["drop"], "drop", n["drop"], "підсистеми → неіснуючий вузол")
    for n in socks:
        if n.get("drop") and n["drop"] not in drop_ids:
            add("sockets", n["drop"], "drop", n["drop"], "сокети → неіснуючий вузол")

    return {"ok": len(issues) == 0, "issues": issues}


@app.get("/config/subsystems")
def get_config_subsystems():
    with open(SUBSYSTEMS_CFG_PATH) as f:
        return json.load(f)


@app.get("/points")
def get_points():
    return _read_points()


@app.get("/points/values")
def get_point_values():
    """Current values from Redis: {point_id: {v, q}} + heartbeat states."""
    try:
        pts = _read_points()
        with open(SOCKETS_PATH) as f:
            socks_data = json.load(f)

        # pre-build socket_id → [heartbeat points] (avoid O(N²) inner scan)
        sock_pts = {}
        for p in pts:
            if p.get("param") == "heartbeat" and p.get("socket"):
                sock_pts.setdefault(p["socket"], []).append(p)

        # collect every redis op into one pipeline
        pipe = _redis.pipeline()
        pids = []
        for p in pts:
            pids.append(p["id"])
            pipe.hgetall(f"point:{p['id']}")
        svcs = []
        for node in socks_data:
            for s in (node.get("software") or []):
                name = s.get("name")
                if not name:
                    continue
                svcs.append((s["id"], name))
                pipe.ttl(f"heartbeat:{name}")
        results = pipe.execute()

        n = len(pids)
        result = {}
        for pid, d in zip(pids, results[:n]):
            if d:
                result[str(pid)] = {"v": d.get("value"), "q": d.get("quality", "INIT")}
        for (sid, _name), ttl in zip(svcs, results[n:]):
            alive = ttl > 0
            for p in sock_pts.get(sid, []):
                result[str(p["id"])] = {
                    "v": "1" if alive else "0",
                    "q": "GOOD" if alive else "NODATA"
                }
        return result
    except Exception as e:
        raise HTTPException(503, str(e))


class PointIn(BaseModel):
    id: int
    type: str = "analog"
    hb_service: str = ""
    socket: str = ""
    param: str = ""
    object: str = ""
    drop: str = ""
    system: str = ""
    pointname: str
    unit: str = ""
    min: float = 0
    max: float = 100
    warn_min: float = 0
    warn_max: float = 100
    alarm_min: float = 0
    alarm_max: float = 100
    deadband: float = 0
    interval: int = 1
    onArchive: int = 1
    archive_interval: int = 0
    archive_on_change: int = 1
    # discrete-only fields
    normal_value: int = 0
    severity:     str = "none"   # "none" | "warn" | "alarm"
    label_0:      str = ""
    label_1:      str = ""
    # calculated-only fields
    formula:      str = ""
    # control-only fields
    formula_on:             str   = ""
    formula_off:            str   = ""
    transport:              str   = "mqtt"
    target:                 str   = ""
    feedback_id:            Optional[int] = None
    feedback_timeout_ticks: int   = 3


_ANALOG_ONLY     = {"unit", "min", "max", "warn_min", "warn_max",
                    "alarm_min", "alarm_max", "deadband",
                    "archive_on_change", "archive_interval", "interval"}
_DISCRETE_ONLY   = {"normal_value", "severity", "label_0", "label_1"}
_CALCULATED_ONLY = {"formula"}
_CONTROL_ONLY    = {"formula_on", "formula_off", "transport", "target",
                    "feedback_id", "feedback_timeout_ticks"}
_OPMODE_STRIP    = _ANALOG_ONLY | _DISCRETE_ONLY | _CALCULATED_ONLY | _CONTROL_ONLY | {"socket", "param", "hb_service"}


def _point_dict(p: PointIn) -> dict:
    d = {k: v for k, v in p.model_dump().items() if v != "" and v is not None}
    t = d.get("type", "analog")
    if t == "discrete":
        for k in _ANALOG_ONLY | _CALCULATED_ONLY | _CONTROL_ONLY:
            d.pop(k, None)
    elif t == "calculated":
        for k in _DISCRETE_ONLY | _CONTROL_ONLY:
            d.pop(k, None)
    elif t == "state_calc":
        # keeps only `formula` (+ identity, onArchive); state comes from the expr
        for k in _ANALOG_ONLY | _DISCRETE_ONLY | _CONTROL_ONLY:
            d.pop(k, None)
    elif t == "control":
        for k in _ANALOG_ONLY | _DISCRETE_ONLY | _CALCULATED_ONLY | {"onArchive"}:
            d.pop(k, None)
    elif t == "operation_mode":
        for k in _OPMODE_STRIP:
            d.pop(k, None)
    else:
        for k in _DISCRETE_ONLY | _CALCULATED_ONLY | _CONTROL_ONLY:
            d.pop(k, None)
    return d


@app.post("/points")
def create_point(p: PointIn):
    points = _read_points()
    if any(x["id"] == p.id for x in points):
        raise HTTPException(409, f"Point id={p.id} already exists")
    points.append(_point_dict(p))
    _write_points(points)
    return {"ok": True}


@app.put("/points/{point_id}")
def update_point(point_id: int, p: PointIn):
    points = _read_points()
    for i, x in enumerate(points):
        if x["id"] == point_id:
            points[i] = _point_dict(p)
            _write_points(points)
            if p.id != point_id:   # id changed → drop the stale Redis key
                with contextlib.suppress(Exception):
                    _redis.delete(f"point:{point_id}")
            return {"ok": True}
    raise HTTPException(404, f"Point {point_id} not found")


@app.delete("/points/{point_id}")
def delete_point(point_id: int):
    points = _read_points()
    new = [x for x in points if x["id"] != point_id]
    if len(new) == len(points):
        raise HTTPException(404, f"Point {point_id} not found")
    _write_points(new)
    # purge the Redis runtime key too — otherwise it lingers as an orphan
    with contextlib.suppress(Exception):
        _redis.delete(f"point:{point_id}")
    return {"ok": True}


@app.get("/points/redis-orphans")
def get_redis_orphans():
    """Redis point:* keys that have no backing entry in points.json — leftover
    'ghost' params (e.g. from removed sim/test points). Read-only inventory."""
    try:
        cfg_ids = {int(p["id"]) for p in _read_points()}
    except Exception as e:
        raise HTTPException(500, f"points.json read error: {e}")
    orphans = []
    for key in _redis.scan_iter(match="point:*", count=500):
        try:
            pid = int(key.split(":", 1)[1])
        except (ValueError, IndexError):
            continue
        if pid in cfg_ids:
            continue
        d = _redis.hgetall(key) or {}
        orphans.append({
            "id":        pid,
            "value":     d.get("value"),
            "quality":   d.get("quality"),
            "ts":        d.get("ts"),
            "pointname": d.get("pointname"),
            "system":    d.get("system"),
        })
    orphans.sort(key=lambda x: x["id"])
    return orphans


@app.delete("/points/redis/{point_id}")
def delete_redis_point(point_id: int):
    """Delete a single Redis point:* key (orphan cleanup)."""
    n = _redis.delete(f"point:{point_id}")
    if not n:
        raise HTTPException(404, f"Redis key point:{point_id} not found")
    return {"ok": True, "deleted": n}


@app.post("/points/reload")
def reload_points():
    """Apply points.json + reconcile the Redis base: flush every point:* key
    (drops stale / orphaned / drifted data), then restart the data services so
    they repopulate from the current config. Pressing this with no config change
    acts as base maintenance."""
    results = {}
    # 1. wipe the parameter base in Redis (clean slate)
    try:
        keys = list(_redis.scan_iter(match="point:*", count=500))
        results["redis"] = f"flushed {_redis.delete(*keys) if keys else 0} point keys"
    except Exception as e:
        results["redis"] = f"error: {e}"
    # 2. restart data services → repopulate from current points.json
    for name in ["infrabox-core", "infrabox-simulator", "infrabox-selfdiagnostic"]:
        try:
            c = _docker().containers.get(name)
            c.restart()
            results[name] = "restarted"
        except Exception as e:
            results[name] = f"error: {e}"
    return results
