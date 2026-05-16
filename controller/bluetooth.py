"""Bluetooth adapter management — bluetoothctl wrapper, rfkill, status."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import BT_CONTROLLER, BT_DEVICE_LINE

router = APIRouter(prefix="/api/bluetooth", tags=["bluetooth"])


class BluetoothAliasBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=248)


class BluetoothDiscoverableBody(BaseModel):
    enabled: bool = True
    minutes: float | None = Field(
        default=5,
        ge=0,
        le=60,
        description="Discoverable window in minutes; 0 lets the stack use its default timeout.",
    )


def _bluetoothctl(commands: list[str]) -> tuple[int, str]:
    exe = shutil.which("bluetoothctl")
    if not exe:
        return -1, ""
    script = "\n".join(commands + ["quit"]) + "\n"
    proc = subprocess.run(
        [exe],
        input=script,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, out


def _parse_bt_device_lines(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for line in text.splitlines():
        m = BT_DEVICE_LINE.match(line.strip())
        if m:
            found.append({"address": m.group(1).upper(), "name": m.group(2).strip() or "(unknown)"})
    return found


def _parse_bt_show(text: str) -> dict[str, Any]:
    adapter: dict[str, Any] = {
        "address": "",
        "name": "",
        "alias": "",
        "powered": None,
        "discoverable": False,
        "pairable": False,
    }
    for line in text.splitlines():
        line = line.strip()
        m = BT_CONTROLLER.match(line)
        if m:
            adapter["address"] = m.group(1).upper()
            continue
        if line.startswith("Name:"):
            adapter["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Alias:"):
            adapter["alias"] = line.split(":", 1)[1].strip()
        elif line.startswith("Powered:"):
            adapter["powered"] = "yes" in line.lower()
        elif line.startswith("Discoverable:"):
            adapter["discoverable"] = "yes" in line.lower()
        elif line.startswith("Pairable:"):
            adapter["pairable"] = "yes" in line.lower()
    return adapter


def _rfkill_unblock_bluetooth() -> None:
    exe = shutil.which("rfkill")
    if not exe:
        return
    subprocess.run([exe, "unblock", "bluetooth"], capture_output=True, text=True, timeout=15)


def _rfkill_bluetooth_blocked() -> bool:
    """True if rfkill reports Bluetooth hci soft- or hard-blocked."""
    exe = shutil.which("rfkill")
    if not exe:
        return False
    proc = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=10)
    for chunk in re.split(r"\n(?=\d+:)", proc.stdout or ""):
        if not re.search(r"Bluetooth", chunk, re.I):
            continue
        if re.search(r"Soft blocked:\s*yes", chunk, re.I):
            return True
        if re.search(r"Hard blocked:\s*yes", chunk, re.I):
            return True
    return False


def _connected_fallback(all_devices: list[dict[str, str]]) -> list[dict[str, str]]:
    connected: list[dict[str, str]] = []
    for d in all_devices:
        _, inf = _bluetoothctl([f"info {d['address']}"])
        if "Connected: yes" in inf:
            connected.append(d)
    return connected


def check_bt_audio_connected() -> bool:
    """True if any Bluetooth device is connected (A2DP likely active)."""
    code, out = _bluetoothctl(["devices Connected"])
    if code != 0 or "Invalid" in out:
        _, all_out = _bluetoothctl(["devices"])
        all_dev = _parse_bt_device_lines(all_out)
        return bool(_connected_fallback(all_dev))
    return bool(_parse_bt_device_lines(out))


def bluetooth_status() -> dict[str, Any]:
    if not shutil.which("bluetoothctl"):
        return {
            "available": False,
            "error": "bluetoothctl not found (install bluez)",
            "rfkill_blocked": False,
            "adapter_ready": False,
            "adapter": {},
            "paired": [],
            "connected": [],
        }

    code, show_out = _bluetoothctl(["show"])
    if code != 0 and "Controller" not in show_out:
        return {
            "available": False,
            "error": (show_out or "bluetooth adapter unavailable").strip()[:500],
            "rfkill_blocked": _rfkill_bluetooth_blocked(),
            "adapter_ready": False,
            "adapter": {},
            "paired": [],
            "connected": [],
        }

    adapter = _parse_bt_show(show_out)

    _, paired_out = _bluetoothctl(["paired-devices"])
    paired = _parse_bt_device_lines(paired_out)

    code_c, conn_out = _bluetoothctl(["devices Connected"])
    connected = _parse_bt_device_lines(conn_out)
    if code_c != 0 or re.search(r"(?i)\b(invalid|unknown command)\b", conn_out):
        _, all_out = _bluetoothctl(["devices"])
        all_dev = _parse_bt_device_lines(all_out)
        connected = _connected_fallback(all_dev) if all_dev else []

    name_map = {p["address"]: p["name"] for p in paired}
    for c in connected:
        if c["name"] == "(unknown)" or not c["name"]:
            c["name"] = name_map.get(c["address"], c["name"])

    rfkill_blocked = _rfkill_bluetooth_blocked()
    adapter_ready = adapter.get("powered") is True and not rfkill_blocked

    return {
        "available": True,
        "error": None,
        "rfkill_blocked": rfkill_blocked,
        "adapter_ready": adapter_ready,
        "adapter": adapter,
        "paired": paired,
        "connected": connected,
    }


def _ensure_bluetooth_adapter_on() -> None:
    """Unblock rfkill and power on controller."""
    _rfkill_unblock_bluetooth()
    code, out = _bluetoothctl(["power on"])
    if code != 0:
        raise HTTPException(
            status_code=500,
            detail=(out or "bluetoothctl power on failed — check bluetooth.service and hci0")[-500:],
        )


# --- Routes ---


@router.post("/alias")
def post_bluetooth_alias(body: BluetoothAliasBody) -> dict[str, Any]:
    if not shutil.which("bluetoothctl"):
        raise HTTPException(status_code=503, detail="bluetoothctl not available")
    name = re.sub(r"[\x00-\x1f]", "", body.name.strip())
    if not name:
        raise HTTPException(status_code=400, detail="empty name")
    code, out = _bluetoothctl([f"system-alias {name}"])
    if code != 0:
        raise HTTPException(
            status_code=500,
            detail=(out or "system-alias failed; old BlueZ may need /etc/bluetooth/main.conf Name=")[-500:],
        )
    return {"ok": True, "bluetooth": bluetooth_status()}


@router.post("/enable")
def post_bluetooth_enable() -> dict[str, Any]:
    """Unblock rfkill (soft block) and turn the adapter on."""
    if not shutil.which("bluetoothctl"):
        raise HTTPException(status_code=503, detail="bluetoothctl not available")
    _ensure_bluetooth_adapter_on()
    return {"ok": True, "bluetooth": bluetooth_status()}


@router.post("/discoverable")
def post_bluetooth_discoverable(body: BluetoothDiscoverableBody) -> dict[str, Any]:
    if not shutil.which("bluetoothctl"):
        raise HTTPException(status_code=503, detail="bluetoothctl not available")
    if body.enabled:
        _rfkill_unblock_bluetooth()
        cmds = ["power on", "pairable on"]
        if body.minutes is not None and body.minutes > 0:
            cmds.append(f"discoverable-timeout {int(body.minutes * 60)}")
        cmds.append("discoverable on")
    else:
        cmds = ["discoverable off"]
    code, out = _bluetoothctl(cmds)
    if code != 0:
        raise HTTPException(status_code=500, detail=(out or "bluetoothctl failed")[-400:])
    return {"ok": True, "bluetooth": bluetooth_status()}
