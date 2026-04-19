import json
import os
import pathlib
import shutil
import socket
import subprocess

import docker as docker_sdk
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SELF        = os.environ.get("HOSTNAME", "infrabox-adm")
CONFIG_FILE = pathlib.Path("/app/config/infrabox.json")


# ── helpers ───────────────────────────────────────────────────────────────────
def client():
    return docker_sdk.from_env()


def _get(name: str):
    try:
        return client().containers.get(name)
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


def _docker_bin():
    return shutil.which("docker") or "/usr/bin/docker"


def _compose_run(workdir: str, *args, timeout: int = 300) -> dict:
    r = subprocess.run(
        [_docker_bin(), "compose"] + list(args),
        cwd=workdir, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "DOCKER_HOST": "unix:///var/run/docker.sock"},
    )
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or r.stdout or "Помилка")[-2000:])
    return {"ok": True, "out": (r.stdout or "")[-1000:]}


# ── infrabox.json ─────────────────────────────────────────────────────────────
def _read_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def _write_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


@app.get("/config")
def get_config():
    cfg = _read_config()
    if not cfg:
        raise HTTPException(404, "infrabox.json not found or invalid")
    return cfg


class SshKeyIn(BaseModel):
    key: str


@app.patch("/config/hosts/{host_id}/ssh_key")
def set_ssh_key(host_id: str, body: SshKeyIn):
    cfg = _read_config()
    if not cfg:
        raise HTTPException(404, "infrabox.json not found")
    host = next((h for h in cfg.get("hosts", []) if h["id"] == host_id), None)
    if not host:
        raise HTTPException(404, f"Host {host_id!r} not found")
    host["ssh_key"] = body.key.strip()
    _write_config(cfg)
    return {"ok": True}


# ── subsystems (з infrabox.json) ──────────────────────────────────────────────
def _sub_status(workdir: str) -> dict:
    try:
        all_c   = client().containers.list(all=True)
        mine    = [c for c in all_c
                   if (c.labels or {}).get("com.docker.compose.project.working_dir", "") == workdir]
        total   = len(mine)
        running = sum(1 for c in mine if c.status == "running")
        exited  = sum(1 for c in mine if c.status in ("exited", "created"))
        return {"total": total, "running": running, "exited": exited}
    except Exception:
        return {"total": 0, "running": 0, "exited": 0}


def _get_sub(sub_id: str) -> dict:
    cfg = _read_config()
    s = next((x for x in cfg.get("subsystems", []) if x["id"] == sub_id), None)
    if not s:
        raise HTTPException(404, f"Subsystem {sub_id!r} not found")
    return s


@app.get("/subsystems")
def list_subsystems():
    cfg = _read_config()
    return [
        {"id": s["id"], "name": s["name"], "workdir": s["workdir"], **_sub_status(s["workdir"])}
        for s in cfg.get("subsystems", [])
    ]


@app.post("/subsystems/{sub_id}/start")
def sub_start(sub_id: str):
    return _compose_run(_get_sub(sub_id)["workdir"], "up", "-d")


@app.post("/subsystems/{sub_id}/stop")
def sub_stop(sub_id: str):
    return _compose_run(_get_sub(sub_id)["workdir"], "stop")


@app.post("/subsystems/{sub_id}/build")
def sub_build(sub_id: str):
    return _compose_run(_get_sub(sub_id)["workdir"], "up", "-d", "--build", timeout=600)


@app.post("/subsystems/{sub_id}/build/stream")
def sub_build_stream(sub_id: str):
    workdir = _get_sub(sub_id)["workdir"]

    def generate():
        proc = subprocess.Popen(
            [_docker_bin(), "compose", "up", "--build", "-d"],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "DOCKER_HOST": "unix:///var/run/docker.sock"},
        )
        for line in proc.stdout:
            yield line
        proc.wait()
        yield f"\n[EXIT {proc.returncode}]\n"

    return StreamingResponse(generate(), media_type="text/plain")


@app.post("/subsystems/{sub_id}/down")
def sub_down(sub_id: str):
    return _compose_run(_get_sub(sub_id)["workdir"], "down")


# ── containers list ───────────────────────────────────────────────────────────
@app.get("/containers")
def list_containers():
    try:
        containers = client().containers.list(all=True)
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


# ── single container actions ──────────────────────────────────────────────────
@app.post("/containers/{name}/start")
def start(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get(name)
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
    c = _get(name)
    try:
        c.stop(timeout=10)
        return {"ok": True, "status": _safe_status(c)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/containers/{name}/restart")
def restart(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get(name)
    try:
        c.restart(timeout=10)
        return {"ok": True, "status": _safe_status(c)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/containers/{name}")
def remove(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get(name)
    try:
        c.remove(force=True)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/containers/{name}/rebuild")
def rebuild(name: str):
    if name == SELF:
        raise HTTPException(400, "Cannot act on self")
    c = _get(name)
    lbl     = c.labels or {}
    workdir = lbl.get("com.docker.compose.project.working_dir", "")
    service = lbl.get("com.docker.compose.service", "")
    if not workdir or not service:
        raise HTTPException(400, "Container is not managed by docker compose")
    try:
        return _compose_run(workdir, "up", "--build", "-d", service)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── host info & control ───────────────────────────────────────────────────────
def _nsenter(cmd: list[str]):
    return subprocess.Popen(
        ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--"] + cmd
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
        node_name  = (os.environ.get("NODE_NAME")
                      or pathlib.Path("/host_hostname").read_text().strip()
                      or socket.gethostname())
        return {
            "hostname": node_name,
            "uptime":   uptime_str,
            "uptime_s": uptime_s,
            "load":     f"{load_raw[0]} {load_raw[1]} {load_raw[2]}",
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/host/reboot")
def host_reboot():
    try:
        _nsenter(["shutdown", "-r", "now"])
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/host/shutdown")
def host_shutdown():
    try:
        _nsenter(["shutdown", "-h", "now"])
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))
