import logging
import os
import time
import uvicorn

from datetime import datetime
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from modules.arch_client import (
    get_status, get_volumes, get_volume_meta,
    get_events, get_values, get_selfdiag,
    get_config, get_sessions, control
)

app = FastAPI(title="Infrabox Arch Backend")

# --- CORS для фронтенду ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def setup_logger():
    logger = logging.getLogger("arch_backend")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    os.makedirs("/app/log", exist_ok=True)
    file_handler = RotatingFileHandler(
        "/app/log/arch_backend.log",
        maxBytes=1048576,
        backupCount=3
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# --- ROUTES ---
@app.get("/status")
async def status():
    return await get_status()


@app.get("/volumes")
async def volumes():
    return await get_volumes()


@app.get("/volumes/{volume}/meta")
async def volume_meta(volume: str):
    return await get_volume_meta(volume)


@app.get("/volumes/{volume}/events")
async def events(volume: str, point_id: int = None):
    return await get_events(volume, point_id)


@app.get("/volumes/{volume}/values")
async def values(volume: str, point_id: int = None,
                 from_ts: int = None, to_ts: int = None):
    return await get_values(volume, point_id, from_ts, to_ts)


@app.get("/volumes/{volume}/selfdiag")
async def selfdiag(volume: str, point_id: int = None):
    return await get_selfdiag(volume, point_id)


@app.get("/volumes/{volume}/config")
async def config(volume: str):
    return await get_config(volume)


@app.get("/sessions")
async def sessions():
    return await get_sessions()


@app.post("/control/{action}")
async def control_action(action: str):
    return await control(action)


# --- POINTS ---
@app.get("/points")
async def points():
    vols = await get_volumes()
    if not vols:
        return []
    # vols вже відфільтровані і відсортовані по даті
    latest = vols[0]  # перший = найновіший (reverse=True)
    return await get_config(latest)


@app.get("/points/{point_id}/values")
async def point_values(point_id: int, volume: str = None,
                       from_ts: int = None, to_ts: int = None):
    """значення точки — з конкретного тому або останнього"""
    if not volume:
        vols = await get_volumes()
        if not vols:
            return []
        volume = sorted(vols)[-1]
    return await get_values(volume, point_id, from_ts, to_ts)


@app.get("/points/{point_id}/events")
async def point_events(point_id: int, volume: str = None):
    """події точки"""
    if not volume:
        vols = await get_volumes()
        if not vols:
            return []
        volume = sorted(vols)[-1]
    return await get_events(volume, point_id)


@app.get("/points/{point_id}/range")
async def point_range(point_id: int, from_ts: int, to_ts: int):
    """значення точки за період — збирає з усіх томів"""
    vols = await get_volumes()
    if not vols:
        return []

    result = []

    for vol in vols:
        # перевіряємо чи том перетинається з запитаним періодом
        try:
            meta = await get_volume_meta(vol)
            # парсимо час відкриття тому
            opened_at = int(datetime.fromisoformat(
                meta["opened_at"]).timestamp())
            closed_at = meta.get("closed_at")
            if closed_at:
                closed_ts = int(datetime.fromisoformat(closed_at).timestamp())
            else:
                closed_ts = int(time.time())  # поточний том

            # том поза діапазоном — пропускаємо
            if closed_ts < from_ts or opened_at > to_ts:
                continue

            data = await get_values(vol, point_id, from_ts, to_ts)
            result.extend(data)

        except Exception:
            continue

    # сортуємо по часу
    result.sort(key=lambda x: x["ts"])
    return result


@app.get("/points/{point_id}/state_range")
async def point_state_range(point_id: int, from_ts: int, to_ts: int):
    """події точки за період — збирає з усіх томів"""
    vols = await get_volumes()
    if not vols:
        return []

    result = []

    for vol in vols:
        try:
            meta = await get_volume_meta(vol)
            opened_at = int(datetime.fromisoformat(
                meta["opened_at"]).timestamp())
            closed_at = meta.get("closed_at")
            closed_ts = int(datetime.fromisoformat(
                closed_at).timestamp()) if closed_at else int(time.time())

            if closed_ts < from_ts or opened_at > to_ts:
                continue

            data = await get_events(vol, point_id)
            # фільтруємо по часу (events мають ts в мілісекундах)
            data = [e for e in data
                    if from_ts * 1000 <= e.get("ts", 0) <= to_ts * 1000]
            result.extend(data)

        except Exception:
            continue

    result.sort(key=lambda x: x["ts"])
    return result


if __name__ == "__main__":
    log = setup_logger()
    log.info("Arch Backend started")
    uvicorn.run(app, host="0.0.0.0", port=8101, log_level="info")
