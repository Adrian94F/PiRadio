"""Unified device name — sets the shairport-sync (AirPlay) name."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import DEVICE_NAME_FILE, SHAIRPORT_CONF
from .system import run_systemctl, unit_active

router = APIRouter(prefix="/api", tags=["device-name"])


class DeviceNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=248)


def read_device_name() -> str:
    try:
        t = DEVICE_NAME_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    except OSError:
        pass
    return ""


def _set_shairport_name(name: str) -> None:
    """Update name = \"…\" in /etc/shairport-sync.conf general block."""
    if not SHAIRPORT_CONF.is_file():
        return
    text = SHAIRPORT_CONF.read_text(encoding="utf-8")
    new_text = re.sub(r'(name\s*=\s*)"[^"]*"', f'\\1"{name}"', text, count=1)
    if new_text != text:
        SHAIRPORT_CONF.write_text(new_text, encoding="utf-8")


def apply_device_name(name: str) -> dict[str, Any]:
    """Set the device name for AirPlay (shairport-sync)."""
    errors: list[str] = []

    try:
        _set_shairport_name(name)
        if unit_active("shairport-sync.service") == "active":
            run_systemctl(["restart", "shairport-sync.service"])
    except Exception as e:
        errors.append(f"shairport: {e}")

    try:
        DEVICE_NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_NAME_FILE.write_text(name + "\n", encoding="utf-8")
    except OSError:
        pass

    return {"ok": True, "name": name, "errors": errors or None}


# --- Routes ---


@router.get("/device-name")
def get_device_name() -> dict[str, str]:
    return {"name": read_device_name()}


@router.post("/device-name")
def post_device_name(body: DeviceNameBody) -> dict[str, Any]:
    name = re.sub(r"[\x00-\x1f]", "", body.name.strip())
    if not name:
        raise HTTPException(status_code=400, detail="empty name")
    try:
        return apply_device_name(name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
