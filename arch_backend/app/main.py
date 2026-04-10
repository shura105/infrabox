import asyncio
import logging
import os
import time
import uvicorn

from datetime import datetime
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from modules.arch_client import (
    get_status, get_volumes, get_volume_meta,
    get_events, get_values, get_selfdiag,
    get_config, get_sessions, control,
    get_current_values
)

app = FastAPI(title="Infrabox Arch Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHART_MAX_POINTS = 1000


# --- GLOBAL ERROR HANDLER ---
# CORSMiddleware не додає заголовки до unhandled exceptions (500).
# Цей handler перехоплює їх і повертає JSON з CORS заголовками.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers={"Access-Control-Allow-Origin": "*"}
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


def _downsample(data: list, max_points: int) -> list:
    n = len(data)
    if n <= max_points:
        return data

    result = [data[0]]
    bucket_size = (n - 2) / (max_points - 2)

    for i in range(max_points - 2):
        start = int(i * bucket_size) + 1
        end = int((i + 1) * bucket_size) + 1
        bucket = data[start:end]
        if not bucket:
            continue
        prev_y = result[-1]["value"]
        next_y = data[min(end, n - 1)]["value"]
        mid_y = (prev_y + next_y) / 2
        best = max(bucket, key=lambda r: abs(r["value"] - mid_y))
        result.append(best)

    result.append(data[-1])
    return result


def _vol_intersects(meta: dict, from_ts: int, to_ts: int) -> bool:
    try:
        opened_at = int(datetime.fromisoformat(meta["opened_at"]).timestamp())
        closed_at = meta.get("closed_at")
        closed_ts = int(datetime.fromisoformat(
            closed_at).timestamp()) if closed_at else int(time.time())
        return closed_ts >= from_ts and opened_at <= to_ts
    except Exception:
        return False


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
    return await get_config(vols[0])


@app.get("/points/{point_id}/current")
async def point_current(point_id: int):
    return await get_current_values(point_id)


@app.get("/points/{point_id}/values")
async def point_values(point_id: int, volume: str = None,
                       from_ts: int = None, to_ts: int = None):
    if not volume:
        vols = await get_volumes()
        if not vols:
            return []
        volume = vols[0]
    return await get_values(volume, point_id, from_ts, to_ts)


@app.get("/points/{point_id}/events")
async def point_events(point_id: int, volume: str = None):
    if not volume:
        vols = await get_volumes()
        if not vols:
            return []
        volume = vols[0]
    return await get_events(volume, point_id)


@app.get("/points/{point_id}/range")
async def point_range(point_id: int, from_ts: int, to_ts: int,
                      max_points: int = CHART_MAX_POINTS):
    vols = await get_volumes()
    if not vols:
        return []

    metas = await asyncio.gather(
        *[get_volume_meta(v) for v in vols],
        return_exceptions=True
    )

    relevant = [
        v for v, m in zip(vols, metas)
        if not isinstance(m, Exception) and _vol_intersects(m, from_ts, to_ts)
    ]

    if not relevant:
        return []

    results = await asyncio.gather(
        *[get_values(v, point_id, from_ts, to_ts) for v in relevant],
        return_exceptions=True
    )

    result = []
    for data in results:
        if isinstance(data, Exception):
            continue
        result.extend(data)

    result.sort(key=lambda x: x["ts"])
    return _downsample(result, max_points)


@app.get("/points/{point_id}/state_range")
async def point_state_range(point_id: int, from_ts: int, to_ts: int):
    vols = await get_volumes()
    if not vols:
        return []

    metas = await asyncio.gather(
        *[get_volume_meta(v) for v in vols],
        return_exceptions=True
    )

    relevant = [
        v for v, m in zip(vols, metas)
        if not isinstance(m, Exception) and _vol_intersects(m, from_ts, to_ts)
    ]

    if not relevant:
        return []

    results = await asyncio.gather(
        *[get_events(v, point_id) for v in relevant],
        return_exceptions=True
    )

    result = []
    for data in results:
        if isinstance(data, Exception):
            continue
        filtered = [e for e in data
                    if from_ts * 1000 <= e.get("ts", 0) <= to_ts * 1000]
        result.extend(filtered)

    result.sort(key=lambda x: x["ts"])
    return result


if __name__ == "__main__":
    log = setup_logger()
    log.info("Arch Backend started")
    uvicorn.run(app, host="0.0.0.0", port=8101, log_level="warning")
