"""Unified device name — sets Bluetooth alias, shairport-sync, and raspotify names."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import DEVICE_NAME_FILE, RASPOTIFY_CONF, SHAIRPORT_CONF
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


def _set_raspotify_name(name: str) -> None:
    """Set LIBRESPOT_NAME in /etc/raspotify/conf."""
    if not RASPOTIFY_CONF.is_file():
        return
    text = RASPOTIFY_CONF.read_text(encoding="utf-8")
    new_line = f'LIBRESPOT_NAME="{name}"'
    if re.search(r'^\s*LIBRESPOT_NAME\s*=', text, re.M):
        new_text = re.sub(r'^\s*LIBRESPOT_NAME\s*=.*$', new_line, text, count=1, flags=re.M)
    elif re.search(r'^\s*#\s*LIBRESPOT_NAME\s*=', text, re.M):
        new_text = re.sub(r'^(\s*#\s*LIBRESPOT_NAME\s*=.*)$', f'\\1\n{new_line}', text, count=1, flags=re.M)
    else:
        new_text = text.rstrip() + f'\n\n{new_line}\n'
    if new_text != text:
        RASPOTIFY_CONF.write_text(new_text, encoding="utf-8")


def apply_device_name(name: str) -> dict[str, Any]:
    """Set the unified device name across Bluetooth, AirPlay, and Spotify."""
    errors: list[str] = []

    if shutil.which("bluetoothctl"):
        try:
            proc = subprocess.run(
                ["bluetoothctl", "system-alias", name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0:
                errors.append(f"bluetooth: {(proc.stderr or proc.stdout or 'failed')[:200]}")
        except Exception as e:
            errors.append(f"bluetooth: {e}")

    try:
        _set_shairport_name(name)
        if unit_active("shairport-sync.service") == "active":
            run_systemctl(["restart", "shairport-sync.service"])
    except Exception as e:
        errors.append(f"shairport: {e}")

    try:
        _set_raspotify_name(name)
        if unit_active("raspotify.service") == "active":
            run_systemctl(["restart", "raspotify.service"])
    except Exception as e:
        errors.append(f"raspotify: {e}")

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
