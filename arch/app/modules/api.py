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


def _read_file(volume_dir, filename):
    """читає .json або .json.gz"""
    json_path = os.path.join(DATA_DIR, volume_dir, filename)
    gz_path = json_path + ".gz"

    if os.path.exists(json_path):
        with open(json_path) as f:
            lines = f.readlines()
        return [json.loads(line) for line in lines if line.strip()]

    elif os.path.exists(gz_path):
        with gzip.open(gz_path, "rt") as f:
            lines = f.readlines()
        return [json.loads(line) for line in lines if line.strip()]

    raise HTTPException(status_code=404, detail=f"{filename} not found")


def _read_json(volume_dir, filename):
    """читає цілий json файл (не рядки)"""
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
        "current_volume": _volume.get_current_meta()
    }


# --- VOLUMES ---
@app.get("/volumes")
def list_volumes():
    if not os.path.exists(DATA_DIR):
        return []
    volumes = sorted(os.listdir(DATA_DIR), reverse=True)
    return volumes


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
    records = _read_file(volume_dir, "values.json")

    if point_id:
        records = [r for r in records if r.get("point_id") == point_id]
    if from_ts:
        records = [r for r in records if r.get("ts", 0) >= from_ts]
    if to_ts:
        records = [r for r in records if r.get("ts", 0) <= to_ts]

    return records


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
