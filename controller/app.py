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

STATIC_DIR = Path(__file__).resolve().parent / "static"

APLAY_LINE = re.compile(
    r"^card\s+(\d+):\s*(.+?),\s*device\s+(\d+):\s*(.+)$"
)

app = FastAPI(title="Pi Audio Controller")


class AudioSelectBody(BaseModel):
    id: str = Field(..., description='ALSA id from aplay -l, e.g. "1,0" for card 1 device 0')


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
    """Set ALSA default PCM/CTL to hw:CARD,DEV; backup existing /etc/asound.conf once."""
    content = f"""{ASOUND_MARKER} — default PCM for Pi Audio Controller

pcm.!default {{
    type plug
    slave.pcm "hw:{card},{device}"
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
        "units": {u: {"active": unit_active(u)} for u in units},
        "audio": audio_status(),
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
