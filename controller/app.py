#!/usr/bin/env python3
"""
Pi Audio Controller — FastAPI application entrypoint.
Configure modes in /etc/pi-audio-controller/modes.yaml (installed from config/modes.yaml).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .audio import audio_status, router as audio_router
from .bluetooth import bluetooth_status, router as bluetooth_router
from .config import STATIC_DIR, load_modes
from .device_name import read_device_name, router as device_name_router
from .modes import auto_mode_loop, read_auto_mode, read_last_mode_valid, router as modes_router
from .system import unit_active
from .websocket import router as websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(auto_mode_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Pi Audio Controller", lifespan=lifespan)

app.include_router(audio_router)
app.include_router(bluetooth_router)
app.include_router(modes_router)
app.include_router(device_name_router)
app.include_router(websocket_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    data = load_modes()
    modes = data.get("modes") or {}
    return {
        "modes": {
            k: {"description": (modes[k] or {}).get("description", "")}
            for k in modes
        },
        "dashboard_units": list(data.get("dashboard_units") or []),
    }


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    data = load_modes()
    units = list(data.get("dashboard_units") or [])
    return {
        "mode": read_last_mode_valid(),
        "auto_mode": read_auto_mode(),
        "device_name": read_device_name(),
        "units": {u: {"active": unit_active(u)} for u in units},
        "audio": audio_status(),
        "bluetooth": bluetooth_status(),
    }


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
