import contextlib
import json
import os
import pathlib
import shlex
import socket
import subprocess
import tempfile

import docker as docker_sdk
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
                f"cd {wd} && docker-compose --no-ansi build",
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
