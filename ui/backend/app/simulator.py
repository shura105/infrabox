import docker as docker_sdk
from fastapi import APIRouter, HTTPException

router = APIRouter()
CONTAINER = "infrabox-simulator"


def _get_container():
    return docker_sdk.from_env().containers.get(CONTAINER)


@router.get("/api/pub/simulator")
async def simulator_status():
    try:
        return {"status": _get_container().status}
    except Exception as e:
        return {"status": "unknown", "error": str(e)}


@router.post("/api/pub/simulator/start")
async def simulator_start():
    try:
        c = _get_container()
        if c.status == "paused":
            c.unpause()
        elif c.status != "running":
            c.start()
        return {"ok": True, "status": "running"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/pub/simulator/stop")
async def simulator_stop():
    try:
        c = _get_container()
        if c.status == "running":
            c.pause()
        return {"ok": True, "status": "paused"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
