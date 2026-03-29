import logging
import os
from logging.handlers import RotatingFileHandler

import uvicorn
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


if __name__ == "__main__":
    log = setup_logger()
    log.info("Arch Backend started")
    uvicorn.run(app, host="0.0.0.0", port=8101, log_level="info")
