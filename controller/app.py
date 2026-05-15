#!/usr/bin/env python3
"""
Pi Audio Controller — FastAPI API + WebSocket log tail for systemd units.
Configure modes in /etc/pi-audio-controller/modes.yaml (installed from config/modes.yaml).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

CONFIG_DIRS = [
    Path(os.environ.get("PI_AUDIO_CONFIG", "/etc/pi-audio-controller")),
    Path(__file__).resolve().parent.parent / "config",
]

STATE_FILE = Path(
    os.environ.get(
        "PI_AUDIO_STATE",
        "/var/lib/pi-audio-controller/current-mode",
    )
)

AUDIO_STATE_FILE = Path(
    os.environ.get(
        "PI_AUDIO_OUTPUT_STATE",
        "/var/lib/pi-audio-controller/audio-output.json",
    )
)

ASOUND_PATH = Path(os.environ.get("PI_AUDIO_ASOUND", "/etc/asound.conf"))
ASOUND_MARKER = "# Managed by pi-audio-controller"
DEVICE_NAME_FILE = Path(
    os.environ.get(
        "PI_AUDIO_DEVICE_NAME",
        "/var/lib/pi-audio-controller/device-name",
    )
)

SHAIRPORT_CONF = Path("/etc/shairport-sync.conf")
RASPOTIFY_CONF = Path("/etc/raspotify/conf")
STATIC_DIR = Path(__file__).resolve().parent / "static"

APLAY_LINE = re.compile(
    r"^card\s+(\d+):\s*(.+?),\s*device\s+(\d+):\s*(.+)$"
)

BT_CONTROLLER = re.compile(r"^Controller\s+([0-9A-Fa-f:]+)", re.I)
BT_DEVICE_LINE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.*)$")

app = FastAPI(title="Pi Audio Controller")

_mode_monitor_task: asyncio.Task | None = None


async def _auto_mode_loop() -> None:
    """Background task: if current mode is idle and another is streaming, switch."""
    while True:
        await asyncio.sleep(5)
        try:
            detected = await asyncio.get_event_loop().run_in_executor(
                None, detect_active_mode,
            )
            if detected is None:
                continue
            current = read_last_mode_valid()
            if detected != current:
                try:
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(detected + "\n", encoding="utf-8")
                except OSError:
                    pass
        except Exception:
            pass


@app.on_event("startup")
async def _start_mode_monitor() -> None:
    global _mode_monitor_task
    _mode_monitor_task = asyncio.create_task(_auto_mode_loop())


@app.on_event("shutdown")
async def _stop_mode_monitor() -> None:
    if _mode_monitor_task:
        _mode_monitor_task.cancel()
        try:
            await _mode_monitor_task
        except asyncio.CancelledError:
            pass


class DeviceNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=248)


class AudioSelectBody(BaseModel):
    id: str = Field(..., description='ALSA id from aplay -l, e.g. "1,0" for card 1 device 0')


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


def _config_path(name: str) -> Path:
    for d in CONFIG_DIRS:
        p = d / name
        if p.is_file():
            return p
    raise FileNotFoundError(f"Missing {name}; tried: {[str(d / name) for d in CONFIG_DIRS]}")


def _modes_only_str_keys(raw: Any) -> dict[str, Any]:
    """YAML 1.1 parses unquoted `off`/`on`/etc. as booleans — drop non-str keys."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str)}


def load_modes() -> dict[str, Any]:
    with open(_config_path("modes.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data.get("modes"), dict):
        data = {**data, "modes": _modes_only_str_keys(data["modes"])}
    return data


def run_systemctl(args: list[str]) -> tuple[int, str, str]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise RuntimeError("systemctl not found")
    proc = subprocess.run(
        [systemctl, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def unit_active(unit: str) -> str:
    code, out, err = run_systemctl(["is-active", unit])
    if code != 0 and not out:
        return "unknown"
    return out.strip() or "unknown"


def apply_mode(mode: str) -> None:
    data = load_modes()
    modes: dict[str, Any] = data.get("modes") or {}
    if mode not in modes:
        raise ValueError(f"Unknown mode: {mode}")

    spec = modes[mode]
    stop_first = list(spec.get("stop") or [])
    start_after = list(spec.get("start") or [])

    for unit in stop_first:
        code, _, err = run_systemctl(["stop", unit])
        if code != 0:
            raise RuntimeError(f"stop {unit}: {err or 'failed'}")

    for unit in start_after:
        code, _, err = run_systemctl(["start", unit])
        if code != 0:
            raise RuntimeError(f"start {unit}: {err or 'failed'}")

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(mode + "\n", encoding="utf-8")
    except OSError:
        pass


def read_last_mode() -> str | None:
    try:
        t = STATE_FILE.read_text(encoding="utf-8").strip()
        return t or None
    except OSError:
        return None


def read_last_mode_valid() -> str | None:
    """Last mode only if it exists in modes.yaml (drops stale e.g. ``false`` from old YAML bug)."""
    m = read_last_mode()
    if not m:
        return None
    modes = (load_modes().get("modes") or {})
    return m if m in modes else None


# ---------------------------------------------------------------------------
# Stream detection — checks whether each mode is actively streaming
# ---------------------------------------------------------------------------

def _check_process_connections(process_name: str) -> bool:
    """True if *process_name* has ESTABLISHED TCP connections (client streaming)."""
    ss = shutil.which("ss")
    if not ss:
        return False
    try:
        proc = subprocess.run(
            [ss, "-tnpH", "state", "established"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return process_name in (proc.stdout or "")
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_bt_audio_connected() -> bool:
    """True if any Bluetooth device is connected (A2DP likely active)."""
    code, out = _bluetoothctl(["devices Connected"])
    if code != 0 or "Invalid" in out:
        # Fallback for old BlueZ without 'devices Connected'
        _, all_out = _bluetoothctl(["devices"])
        all_dev = _parse_bt_device_lines(all_out)
        return bool(_connected_fallback(all_dev))
    return bool(_parse_bt_device_lines(out))


def _is_mode_active(mode: str) -> bool:
    """Check if the given mode currently has an active stream / connection."""
    data = load_modes()
    spec = (data.get("modes") or {}).get(mode)
    if not spec:
        return False
    detect = spec.get("detect")
    if not detect:
        return False
    if detect.get("process"):
        return _check_process_connections(detect["process"])
    if detect.get("bt_connected"):
        return _check_bt_audio_connected()
    return False


def detect_active_mode() -> str | None:
    """Return the mode that should be active based on stream detection.

    Priority: if the current mode is still active keep it; otherwise pick
    the first mode that has activity.
    """
    data = load_modes()
    modes = data.get("modes") or {}
    current = read_last_mode_valid()

    # If current mode is still streaming — no switch needed.
    if current and _is_mode_active(current):
        return current

    # Find first other mode that is active.
    for name in modes:
        if name == current:
            continue
        if _is_mode_active(name):
            return name

    return None


def list_alsa_outputs() -> list[dict[str, Any]]:
    """Parse `aplay -l` playback devices (USB cards, bcm2835, HDMI, etc.)."""
    aplay = shutil.which("aplay")
    if not aplay:
        return []
    proc = subprocess.run(
        [aplay, "-l"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        m = APLAY_LINE.match(line.strip())
        if not m:
            continue
        card_s, card_name, dev_s, dev_name = m.groups()
        card = int(card_s)
        device = int(dev_s)
        cid = f"{card},{device}"
        out.append(
            {
                "id": cid,
                "card": card,
                "device": device,
                "label": f"Card {card}: {card_name.strip()} — {dev_name.strip()}",
            }
        )
    return out


def read_audio_selection() -> dict[str, Any] | None:
    try:
        raw = AUDIO_STATE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("id"):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _alsa_restart_units() -> list[str]:
    data = load_modes()
    units = data.get("alsa_restart_units")
    if isinstance(units, list) and units:
        return [str(u) for u in units]
    return ["shairport-sync.service", "raspotify.service", "bluealsa.service"]


def restart_active_audio_sinks() -> list[str]:
    restarted: list[str] = []
    for unit in _alsa_restart_units():
        if unit_active(unit) != "active":
            continue
        code, _, err = run_systemctl(["restart", unit])
        if code == 0:
            restarted.append(unit)
        elif err:
            restarted.append(f"{unit}: {err}")
    return restarted


def write_default_asound(card: int, device: int) -> None:
    """Set ALSA default PCM/CTL via dmix (shared access); backup existing /etc/asound.conf once."""
    content = f"""{ASOUND_MARKER} — default PCM for Pi Audio Controller

pcm.!default {{
    type plug
    slave.pcm "dmixer"
}}
pcm.dmixer {{
    type dmix
    ipc_key 1024
    slave {{
        pcm "hw:{card},{device}"
        period_time 0
        period_size 1024
        buffer_size 4096
    }}
}}
ctl.!default {{
    type hw
    card {card}
}}
"""
    ASOUND_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ASOUND_PATH.is_file():
        existing = ASOUND_PATH.read_text(encoding="utf-8", errors="replace")
        if ASOUND_MARKER not in existing:
            bak = ASOUND_PATH.with_name(f"asound.conf.bak.pi-audio-{int(time.time())}")
            bak.write_text(existing, encoding="utf-8")
    ASOUND_PATH.write_text(content, encoding="utf-8")


def apply_audio_output(hw_id: str) -> dict[str, Any]:
    """Validate id against `aplay -l`, persist, write /etc/asound.conf, restart sinks."""
    outputs = list_alsa_outputs()
    valid = next((o for o in outputs if o["id"] == hw_id), None)
    if not valid:
        raise ValueError(f"Unknown output id {hw_id!r}; refresh the device list.")

    card = int(valid["card"])
    device = int(valid["device"])
    write_default_asound(card, device)

    AUDIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_STATE_FILE.write_text(
        json.dumps(
            {
                "id": valid["id"],
                "card": card,
                "device": device,
                "label": valid["label"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    restarted = restart_active_audio_sinks()
    return {"id": valid["id"], "label": valid["label"], "restarted": restarted}


def audio_status() -> dict[str, Any]:
    outputs = list_alsa_outputs()
    current = read_audio_selection()
    cur_id = current.get("id") if current else None
    if cur_id and not any(o["id"] == cur_id for o in outputs):
        current = {**(current or {}), "missing": True}
    return {"outputs": outputs, "current": current}


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
        line = line.strip()
        m = BT_DEVICE_LINE.match(line)
        if m:
            addr, rest = m.group(1), m.group(2).strip()
            found.append({"address": addr.upper(), "name": rest or "(unknown)"})
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
    subprocess.run(
        [exe, "unblock", "bluetooth"],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _rfkill_bluetooth_blocked() -> bool:
    """True if rfkill reports Bluetooth hci soft- or hard-blocked."""
    exe = shutil.which("rfkill")
    if not exe:
        return False
    proc = subprocess.run(
        [exe, "list"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = proc.stdout or ""
    chunks = re.split(r"\n(?=\d+:)", out)
    for ch in chunks:
        if not re.search(r"Bluetooth", ch, re.I):
            continue
        if re.search(r"Soft blocked:\s*yes", ch, re.I):
            return True
        if re.search(r"Hard blocked:\s*yes", ch, re.I):
            return True
    return False


def _connected_fallback(all_devices: list[dict[str, str]]) -> list[dict[str, str]]:
    connected: list[dict[str, str]] = []
    for d in all_devices:
        addr = d["address"]
        _, inf = _bluetoothctl([f"info {addr}"])
        if "Connected: yes" in inf:
            connected.append(d)
    return connected


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
    bad_conn = code_c != 0 or re.search(
        r"(?i)\b(invalid|unknown command)\b",
        conn_out,
    )
    if bad_conn:
        _, all_out = _bluetoothctl(["devices"])
        all_dev = _parse_bt_device_lines(all_out)
        connected = _connected_fallback(all_dev) if all_dev else []

    name_map = {p["address"]: p["name"] for p in paired}
    for c in connected:
        if c["name"] == "(unknown)" or not c["name"]:
            c["name"] = name_map.get(c["address"], c["name"])

    rfkill_blocked = _rfkill_bluetooth_blocked()
    powered_ok = adapter.get("powered") is True
    adapter_ready = powered_ok and not rfkill_blocked

    return {
        "available": True,
        "error": None,
        "rfkill_blocked": rfkill_blocked,
        "adapter_ready": adapter_ready,
        "adapter": adapter,
        "paired": paired,
        "connected": connected,
    }



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
        "device_name": read_device_name(),
        "units": {u: {"active": unit_active(u)} for u in units},
        "audio": audio_status(),
        "bluetooth": bluetooth_status(),
    }


@app.get("/api/audio")
def get_audio() -> dict[str, Any]:
    return audio_status()


@app.post("/api/audio")
def post_audio(body: AudioSelectBody) -> dict[str, Any]:
    hw_id = body.id.strip()
    try:
        result = apply_audio_output(hw_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, **result}


@app.post("/api/mode/{mode}")
def post_mode(mode: str) -> dict[str, str]:
    try:
        apply_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"mode": mode, "ok": "true"}


# ---------------------------------------------------------------------------
# Unified device name — sets Bluetooth alias, shairport-sync, raspotify
# ---------------------------------------------------------------------------

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
    new_text = re.sub(
        r'(name\s*=\s*)"[^"]*"',
        f'\\1"{name}"',
        text,
        count=1,
    )
    if new_text != text:
        SHAIRPORT_CONF.write_text(new_text, encoding="utf-8")


def _set_raspotify_name(name: str) -> None:
    """Set LIBRESPOT_NAME in /etc/raspotify/conf."""
    if not RASPOTIFY_CONF.is_file():
        return
    text = RASPOTIFY_CONF.read_text(encoding="utf-8")
    new_line = f'LIBRESPOT_NAME="{name}"'
    if re.search(r'^\s*LIBRESPOT_NAME\s*=', text, re.M):
        new_text = re.sub(
            r'^\s*LIBRESPOT_NAME\s*=.*$',
            new_line,
            text,
            count=1,
            flags=re.M,
        )
    elif re.search(r'^\s*#\s*LIBRESPOT_NAME\s*=', text, re.M):
        new_text = re.sub(
            r'^(\s*#\s*LIBRESPOT_NAME\s*=.*)$',
            f'\\1\n{new_line}',
            text,
            count=1,
            flags=re.M,
        )
    else:
        new_text = text.rstrip() + f'\n\n{new_line}\n'
    if new_text != text:
        RASPOTIFY_CONF.write_text(new_text, encoding="utf-8")


def apply_device_name(name: str) -> dict[str, Any]:
    """Set the unified device name across Bluetooth, AirPlay, and Spotify."""
    errors: list[str] = []

    # Bluetooth
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

    # Shairport-sync
    try:
        _set_shairport_name(name)
        if unit_active("shairport-sync.service") == "active":
            run_systemctl(["restart", "shairport-sync.service"])
    except Exception as e:
        errors.append(f"shairport: {e}")

    # Raspotify
    try:
        _set_raspotify_name(name)
        if unit_active("raspotify.service") == "active":
            run_systemctl(["restart", "raspotify.service"])
    except Exception as e:
        errors.append(f"raspotify: {e}")

    # Persist
    try:
        DEVICE_NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_NAME_FILE.write_text(name + "\n", encoding="utf-8")
    except OSError:
        pass

    return {"ok": True, "name": name, "errors": errors or None}


@app.get("/api/device-name")
def get_device_name() -> dict[str, str]:
    return {"name": read_device_name()}


@app.post("/api/device-name")
def post_device_name(body: DeviceNameBody) -> dict[str, Any]:
    name = re.sub(r"[\x00-\x1f]", "", body.name.strip())
    if not name:
        raise HTTPException(status_code=400, detail="empty name")
    try:
        return apply_device_name(name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/bluetooth/alias")
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


def _ensure_bluetooth_adapter_on() -> None:
    """Unblock rfkill (if needed) and power on controller — safe to call repeatedly."""
    _rfkill_unblock_bluetooth()
    code, out = _bluetoothctl(["power on"])
    if code != 0:
        raise HTTPException(
            status_code=500,
            detail=(out or "bluetoothctl power on failed — check bluetooth.service and hci0")[-500:],
        )


@app.post("/api/bluetooth/enable")
def post_bluetooth_enable() -> dict[str, Any]:
    """Unblock rfkill (soft block) and turn the adapter on (`power on`)."""
    if not shutil.which("bluetoothctl"):
        raise HTTPException(status_code=503, detail="bluetoothctl not available")
    _ensure_bluetooth_adapter_on()
    return {"ok": True, "bluetooth": bluetooth_status()}


@app.post("/api/bluetooth/discoverable")
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


async def _journal_lines(unit: str) -> asyncio.subprocess.Process:
    journalctl = shutil.which("journalctl")
    if not journalctl:
        raise RuntimeError("journalctl not found")
    return await asyncio.create_subprocess_exec(
        journalctl,
        "-u",
        unit,
        "-f",
        "-n",
        "80",
        "--no-pager",
        "-o",
        "short-iso",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    try:
        payload = await ws.receive_text()
        msg = json.loads(payload)
        unit = msg.get("unit")
        if not unit or not isinstance(unit, str):
            await ws.send_text(json.dumps({"error": "expected {\"unit\": \"name.service\"}"}))
            await ws.close(code=4000)
            return
    except (json.JSONDecodeError, WebSocketDisconnect):
        await ws.close(code=4000)
        return

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await _journal_lines(unit)
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            await ws.send_text(line.decode("utf-8", errors="replace").rstrip("\n"))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await ws.send_text(json.dumps({"error": str(e)}))
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
