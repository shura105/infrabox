import asyncio
import logging
import os
import threading
import time
import uvicorn

REDIS_HOST = os.environ.get("REDIS_HOST", "infrabox-redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))


def _heartbeat_thread():
    import redis as redis_sync
    r = None
    while True:
        try:
            if r is None:
                r = redis_sync.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            r.set("heartbeat:infrabox-arch-backend", int(time.time()), ex=25)
        except Exception:
            r = None
        time.sleep(5)

from datetime import datetime
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from modules.arch_client import (
    get_status, get_volumes, get_volume_meta,
    get_events, get_values, get_selfdiag,
    get_config, get_sessions, control,
    get_current_values,
    get_arch_config, set_arch_depth,
    get_volumes_sizes
)

app = FastAPI(title="Infrabox Arch Backend")


@app.on_event("startup")
async def startup():
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHART_MAX_POINTS = 1000

# обмежуємо кількість одночасних range-запитів:
# кожен fetch_range робить десятки HTTP-викликів до arch,
# тому більше 2 паралельних операцій вичерпують пул з'єднань
_range_semaphore = asyncio.Semaphore(2)


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


async def _run_or_cancel(request: Request, coro):
    """
    Виконує корутину як task.
    Якщо клієнт відключився — скасовує task і повертає [].
    Звільняє семафорні слоти миттєво при Back.
    """
    task = asyncio.create_task(coro)
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            return []
        await asyncio.sleep(0.05)
    return await task


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

        # null-записи — маркери розриву, зберігаємо без LTTB
        nulls = [r for r in bucket if r["value"] is None]
        if nulls:
            result.append(nulls[0])
            continue

        prev_y = result[-1]["value"]
        next_y = data[min(end, n - 1)]["value"]
        if prev_y is None or next_y is None:
            result.append(bucket[0])
            continue

        mid_y = (prev_y + next_y) / 2
        best = max(bucket, key=lambda r: abs(r["value"] - mid_y))
        result.append(best)

    result.append(data[-1])
    return result


# Максимальна тривалість тому + запас (24h + 5хв).
# Використовується для фільтрації томів за іменем без HTTP-запитів metadata.
_MAX_VOL_DURATION = 24 * 3600 + 300


def _vol_name_intersects(vol_name: str, from_ts: int, to_ts: int) -> bool:
    """Фільтр тому за іменем (формат YYYY-MM-DD_HH-MM-SS, локальний час).
    Не робить жодних HTTP-запитів — замінює _vol_intersects + get_volume_meta."""
    try:
        opened_at = int(datetime.strptime(vol_name, "%Y-%m-%d_%H-%M-%S").timestamp())
        closed_at = min(opened_at + _MAX_VOL_DURATION, int(time.time()))
        return closed_at >= from_ts and opened_at <= to_ts
    except Exception:
        return False


async def _fetch_range(point_id: int, from_ts: int, to_ts: int,
                       max_points: int = CHART_MAX_POINTS):
    async with _range_semaphore:
        vols = await get_volumes()
        if not vols:
            return []

        relevant = [v for v in vols if _vol_name_intersects(v, from_ts, to_ts)]

        if not relevant:
            return []

        # Для великих діапазонів обмежуємо кількість записів per-volume
        # щоб не передавати зайві дані через HTTP (архіватор зробить stride)
        duration = to_ts - from_ts
        if duration > 3600 and len(relevant) > 0:
            per_vol_max = max(200, (max_points * 2) // len(relevant))
        else:
            per_vol_max = None

        results = await asyncio.gather(
            *[get_values(v, point_id, from_ts, to_ts, max_records=per_vol_max)
              for v in relevant],
            return_exceptions=True
        )

        result = []
        for data in results:
            if isinstance(data, Exception):
                continue
            result.extend(data)

        result.sort(key=lambda x: x["ts"])
        return _downsample(result, max_points)


async def _fetch_state_range(point_id: int, from_ts: int, to_ts: int):
    vols = await get_volumes()
    if not vols:
        return []

    relevant = [v for v in vols if _vol_name_intersects(v, from_ts, to_ts)]

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


# --- ROUTES ---

@app.get("/status")
async def status():
    return await get_status()


@app.get("/volumes")
async def volumes():
    return await get_volumes()


@app.get("/volumes-sizes")
async def volumes_sizes():
    return await get_volumes_sizes()


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


# --- ARCH CONFIG ---
@app.get("/arch-config")
async def arch_config():
    return await get_arch_config()


class DepthBody(BaseModel):
    max_days: int

@app.post("/arch-config/depth")
async def arch_config_depth(body: DepthBody):
    return await set_arch_depth(body.max_days)


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
async def point_range(request: Request, point_id: int,
                      from_ts: int, to_ts: int,
                      max_points: int = CHART_MAX_POINTS):
    return await _run_or_cancel(
        request,
        _fetch_range(point_id, from_ts, to_ts, max_points)
    )


@app.get("/points/{point_id}/state_range")
async def point_state_range(request: Request, point_id: int,
                            from_ts: int, to_ts: int):
    return await _run_or_cancel(
        request,
        _fetch_state_range(point_id, from_ts, to_ts)
    )


if __name__ == "__main__":
    log = setup_logger()
    log.info("Arch Backend started")
    uvicorn.run(app, host="0.0.0.0", port=8101, log_level="warning")