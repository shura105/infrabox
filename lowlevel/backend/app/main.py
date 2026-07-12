"""
infrabox-lowlevel — конструктор пристроїв нижнього рівня.
FastAPI-сервіс: керує проєктами пристроїв (папки на диску).
"""

import os
import threading
import time

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth_guard import require_admin
from app import store, kb

SERVICE_NAME = os.environ.get("SERVICE_NAME", "infrabox-lowlevel")

app = FastAPI(title=SERVICE_NAME)


# ── heartbeat у Redis (як інші сервіси, TTL 25с) ──────────────────────────────
def _heartbeat_thread():
    import redis as redis_sync
    r = None
    while True:
        try:
            if r is None:
                r = redis_sync.Redis(
                    host=os.environ.get("REDIS_HOST", "infrabox-redis"),
                    port=int(os.environ.get("REDIS_PORT", 6379)),
                    decode_responses=True,
                )
            r.set(f"heartbeat:{SERVICE_NAME}", int(time.time()), ex=25)
        except Exception:
            r = None
        time.sleep(5)


@app.on_event("startup")
def _startup():
    store.ensure_dirs()
    threading.Thread(target=_heartbeat_thread, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok"}


# ── пристрої (проєкти) ────────────────────────────────────────────────────────
class DeviceCreate(BaseModel):
    name: str


@app.get("/devices")
def list_devices(_: dict = Depends(require_admin)):
    return {"devices": store.list_devices()}


@app.post("/devices", status_code=201)
def create_device(body: DeviceCreate, _: dict = Depends(require_admin)):
    try:
        return store.create_device(body.name)
    except store.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/devices/{device_id}")
def get_device(device_id: str, _: dict = Depends(require_admin)):
    dev = store.get_device(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


@app.delete("/devices/{device_id}")
def delete_device(device_id: str, _: dict = Depends(require_admin)):
    if not store.delete_device(device_id):
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return {"ok": True}


# ── опис ──────────────────────────────────────────────────────────────────────
class DescriptionUpdate(BaseModel):
    text: str = ""


@app.put("/devices/{device_id}/description")
def set_description(device_id: str, body: DescriptionUpdate, _: dict = Depends(require_admin)):
    dev = store.update_description(device_id, body.text)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


class HardwareUpdate(BaseModel):
    hardware: str | None = None


@app.put("/devices/{device_id}/hardware")
def set_hardware(device_id: str, body: HardwareUpdate, _: dict = Depends(require_admin)):
    if body.hardware and kb.get_equipment(body.hardware) is None:
        raise HTTPException(status_code=400, detail="Обладнання не знайдено в базі знань")
    dev = store.update_hardware(device_id, body.hardware)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


class SchemeImageUpdate(BaseModel):
    image: str | None = None


@app.put("/devices/{device_id}/scheme_image")
def set_scheme_image(device_id: str, body: SchemeImageUpdate, _: dict = Depends(require_admin)):
    dev = store.get_device(device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    if body.image:
        hw = dev.get("hardware")
        eq = kb.get_equipment(hw) if hw else None
        if not eq or body.image not in (eq.get("images") or []):
            raise HTTPException(status_code=400, detail="Зображення не належить обраному контролеру")
    return store.update_scheme_image(device_id, body.image)


class PinsUpdate(BaseModel):
    pins: list[dict] = []


@app.put("/devices/{device_id}/pins")
def set_pins(device_id: str, body: PinsUpdate, _: dict = Depends(require_admin)):
    dev = store.update_pins(device_id, body.pins)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


class ProgramUpdate(BaseModel):
    program: dict = {}


@app.put("/devices/{device_id}/program")
def set_program(device_id: str, body: ProgramUpdate, _: dict = Depends(require_admin)):
    dev = store.update_program(device_id, body.program)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


class CodeUpdate(BaseModel):
    code: str = ""


@app.put("/devices/{device_id}/code")
def set_code(device_id: str, body: CodeUpdate, _: dict = Depends(require_admin)):
    dev = store.update_code(device_id, body.code)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


@app.post("/devices/{device_id}/enclosure/{kind}")
async def upload_enclosure(device_id: str, kind: str, file: UploadFile = File(...), _: dict = Depends(require_admin)):
    data = await file.read()
    try:
        dev = store.save_enclosure(device_id, kind, file.filename, data)
    except store.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


@app.get("/devices/{device_id}/enclosure/{kind}")
def download_enclosure(device_id: str, kind: str, _: dict = Depends(require_admin)):
    path = store.enclosure_path(device_id, kind)
    if not path:
        raise HTTPException(status_code=404, detail="Файл не знайдено")
    return FileResponse(path, filename=os.path.basename(path))


@app.delete("/devices/{device_id}/enclosure/{kind}")
def delete_enclosure(device_id: str, kind: str, _: dict = Depends(require_admin)):
    dev = store.delete_enclosure(device_id, kind)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


class CodeBlocksUpdate(BaseModel):
    blocks: list[dict] = []


@app.put("/devices/{device_id}/code_blocks")
def set_code_blocks(device_id: str, body: CodeBlocksUpdate, _: dict = Depends(require_admin)):
    dev = store.update_code_blocks(device_id, body.blocks)
    if dev is None:
        raise HTTPException(status_code=404, detail="Пристрій не знайдено")
    return dev


@app.get("/points")
def list_points(_: dict = Depends(require_admin)):
    """Точки Infrabox для прив'язки сигналів і побудови MQTT-топіків."""
    pts = store.list_points()
    return {"points": [
        {
            "id": p.get("id"), "pointname": p.get("pointname"), "type": p.get("type"),
            "object": p.get("object"), "system": p.get("system"), "drop": p.get("drop"),
            "min": p.get("min"), "max": p.get("max"),
            "deadband": p.get("deadband"), "unit": p.get("unit"),
        }
        for p in pts
    ]}


# ── база знань обладнання ─────────────────────────────────────────────────────
class EquipmentCreate(BaseModel):
    type: str
    info: str = ""


class EquipmentUpdate(BaseModel):
    type: str | None = None
    info: str | None = None
    pins: list[dict] | None = None
    platform: str | None = None


@app.get("/knowledge")
def list_equipment(_: dict = Depends(require_admin)):
    return {"equipment": kb.list_equipment()}


@app.post("/knowledge", status_code=201)
def create_equipment(body: EquipmentCreate, _: dict = Depends(require_admin)):
    try:
        return kb.create_equipment(body.type, body.info)
    except store.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/knowledge/{eid}")
def get_equipment(eid: str, _: dict = Depends(require_admin)):
    rec = kb.get_equipment(eid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")
    return rec


@app.put("/knowledge/{eid}")
def update_equipment(eid: str, body: EquipmentUpdate, _: dict = Depends(require_admin)):
    try:
        rec = kb.update_equipment(eid, body.type, body.info, body.pins, body.platform)
    except store.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if rec is None:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")
    return rec


@app.delete("/knowledge/{eid}")
def delete_equipment(eid: str, _: dict = Depends(require_admin)):
    if not kb.delete_equipment(eid):
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")
    return {"ok": True}


@app.post("/knowledge/{eid}/images")
async def upload_images(eid: str, files: list[UploadFile] = File(...), _: dict = Depends(require_admin)):
    rec = kb.get_equipment(eid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")
    for f in files:
        data = await f.read()
        try:
            rec = kb.add_image(eid, f.filename, data)
        except store.ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return rec


@app.get("/knowledge/{eid}/images/{name}")
def get_image(eid: str, name: str, _: dict = Depends(require_admin)):
    path = kb.image_path(eid, name)
    if not path:
        raise HTTPException(status_code=404, detail="Зображення не знайдено")
    return FileResponse(path)


@app.delete("/knowledge/{eid}/images/{name}")
def delete_image(eid: str, name: str, _: dict = Depends(require_admin)):
    rec = kb.delete_image(eid, name)
    if rec is None:
        raise HTTPException(status_code=404, detail="Зображення не знайдено")
    return rec
