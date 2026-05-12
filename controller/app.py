#!/usr/bin/env python3
"""
Pi Audio Controller — FastAPI API + WebSocket log tail for systemd units.
Configure modes in /etc/pi-audio-controller/modes.yaml (installed from config/modes.yaml).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Pi Audio Controller")


def _config_path(name: str) -> Path:
    for d in CONFIG_DIRS:
        p = d / name
        if p.is_file():
            return p
    raise FileNotFoundError(f"Missing {name}; tried: {[str(d / name) for d in CONFIG_DIRS]}")


def load_modes() -> dict[str, Any]:
    with open(_config_path("modes.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


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
        "mode": read_last_mode(),
        "units": {u: {"active": unit_active(u)} for u in units},
    }


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
