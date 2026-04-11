import json
import os
import gzip
from fastapi import FastAPI, HTTPException
from datetime import datetime

app = FastAPI()

DATA_DIR = "/app/data"
_volume = None


def set_volume(volume):
    global _volume
    _volume = volume


def _iter_lines(volume_dir, filename):
    """Генератор рядків — не завантажує весь файл в пам'ять."""
    json_path = os.path.join(DATA_DIR, volume_dir, filename)
    gz_path = json_path + ".gz"

    if os.path.exists(json_path):
        with open(json_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line
    elif os.path.exists(gz_path):
        with gzip.open(gz_path, "rt") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line
    else:
        raise HTTPException(status_code=404, detail=f"{filename} not found")


def _read_file(volume_dir, filename):
    """Читає весь файл (для events, selfdiag, commands)."""
    return [json.loads(line) for line in _iter_lines(volume_dir, filename)]


def _read_values_filtered(volume_dir, point_id=None, from_ts=None, to_ts=None):
    """
    Стримінгове читання values.json з раннім виходом.
    Записи впорядковані за часом — зупиняємось як тільки ts > to_ts.
    """
    result = []
    for line in _iter_lines(volume_dir, "values.json"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = r.get("ts", 0)

        if from_ts and ts < from_ts:
            continue
        if to_ts and ts > to_ts:
            break  # файл відсортований — далі читати немає сенсу

        if point_id and r.get("point_id") != point_id:
            continue

        result.append(r)

    return result


def _read_json(volume_dir, filename):
    """Читає цілий json файл (не рядки)."""
    json_path = os.path.join(DATA_DIR, volume_dir, filename)
    gz_path = json_path + ".gz"

    if os.path.exists(json_path):
        with open(json_path) as f:
            return json.load(f)
    elif os.path.exists(gz_path):
        with gzip.open(gz_path, "rt") as f:
            return json.load(f)

    raise HTTPException(status_code=404, detail=f"{filename} not found")


# --- STATUS ---
@app.get("/status")
def get_status():
    if _volume is None:
        return {"status": "not initialized"}
    return {
        "status": "running",
        "recording": _volume.recording,
        "current_volume": _volume.get_current_meta()
    }


# --- VOLUMES ---
@app.get("/volumes")
def list_volumes():
    if not os.path.exists(DATA_DIR):
        return []
    volumes = [
        v for v in os.listdir(DATA_DIR)
        if os.path.isdir(os.path.join(DATA_DIR, v))
    ]
    return sorted(volumes, reverse=True)


# --- CURRENT VOLUME (живі дані) ---
@app.get("/current/values")
def get_current_values(point_id: int = None):
    """
    Читає values з поточного активного тому напряму.
    Швидко — один маленький файл без скану всього архіву.
    """
    if _volume is None:
        raise HTTPException(status_code=503, detail="Not initialized")

    volume_dir = os.path.basename(_volume.current_dir)

    try:
        records = _read_file(volume_dir, "values.json")
    except HTTPException:
        return []

    if point_id is not None:
        records = [r for r in records if r.get("point_id") == point_id]

    return records


# --- VOLUME META ---
@app.get("/volumes/{volume_dir}/meta")
def get_meta(volume_dir: str):
    return _read_json(volume_dir, "meta.json")


# --- EVENTS ---
@app.get("/volumes/{volume_dir}/events")
def get_events(volume_dir: str, point_id: int = None):
    records = _read_file(volume_dir, "events.json")
    if point_id:
        records = [r for r in records if r.get("point_id") == point_id]
    return records


# --- VALUES ---
@app.get("/volumes/{volume_dir}/values")
def get_values(volume_dir: str, point_id: int = None,
               from_ts: int = None, to_ts: int = None):
    return _read_values_filtered(volume_dir, point_id, from_ts, to_ts)


# --- SELFDIAG ---
@app.get("/volumes/{volume_dir}/selfdiag")
def get_selfdiag(volume_dir: str, point_id: int = None):
    records = _read_file(volume_dir, "selfdiag.json")
    if point_id:
        records = [r for r in records if r.get("point_id") == point_id]
    return records


# --- COMMANDS ---
@app.get("/volumes/{volume_dir}/commands")
def get_commands(volume_dir: str):
    return _read_file(volume_dir, "commands.json")


# --- CONFIG SNAP ---
@app.get("/volumes/{volume_dir}/config")
def get_config(volume_dir: str):
    return _read_json(volume_dir, "config_snap.json")


@app.get("/sessions")
def get_sessions():
    path = os.path.join(DATA_DIR, "sessions.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines if line.strip()]


@app.post("/control/stop")
def stop_archivator():
    if _volume is None:
        raise HTTPException(status_code=503, detail="Not initialized")
    _volume.stop()  # атомарна операція з локом всередині Volume
    return {"status": "stopped"}


@app.post("/control/start")
def start_archivator():
    if _volume is None:
        raise HTTPException(status_code=503, detail="Not initialized")
    _volume.start()  # атомарна операція з локом всередині Volume
    return {"status": "started", "volume": os.path.basename(_volume.current_dir)}


@app.post("/control/rotate")
def rotate_volume():
    if _volume is None:
        raise HTTPException(status_code=503, detail="Not initialized")
    if not _volume.recording:
        raise HTTPException(status_code=400, detail="Archivator is stopped")
    _volume.rotate()
    return {"status": "rotated", "volume": os.path.basename(_volume.current_dir)}