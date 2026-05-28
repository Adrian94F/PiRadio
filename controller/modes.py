"""Mode management — apply modes, detect active streams, auto-mode switching."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .bluetooth import check_bt_audio_connected, check_bt_audio_playing
from .config import AUTO_MODE_FILE, STATE_FILE, load_modes
from .system import run_systemctl

router = APIRouter(prefix="/api", tags=["modes"])


def read_auto_mode() -> bool:
    """Return True if auto-mode switching is enabled (default: False)."""
    try:
        return AUTO_MODE_FILE.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def write_auto_mode(enabled: bool) -> None:
    AUTO_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTO_MODE_FILE.write_text("1\n" if enabled else "0\n", encoding="utf-8")


def read_last_mode() -> str | None:
    try:
        t = STATE_FILE.read_text(encoding="utf-8").strip()
        return t or None
    except OSError:
        return None


def read_last_mode_valid() -> str | None:
    """Last mode only if it exists in modes.yaml."""
    m = read_last_mode()
    if not m:
        return None
    modes = load_modes().get("modes") or {}
    return m if m in modes else None


def apply_mode(mode: str) -> None:
    data = load_modes()
    modes: dict[str, Any] = data.get("modes") or {}
    if mode not in modes:
        raise ValueError(f"Unknown mode: {mode}")

    spec = modes[mode]
    stop_first = list(spec.get("stop") or [])
    start_after = list(spec.get("start") or [])
    auto = read_auto_mode()

    if not auto:
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


# --- Stream detection ---


def _check_process_connections(process_name: str) -> bool:
    """True if *process_name* has ESTABLISHED TCP connections."""
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


def _check_state_file(path_str: str) -> bool:
    """True if the state file exists and contains 'playing'."""
    try:
        text = Path(path_str).read_text(encoding="utf-8").strip()
        return text == "playing"
    except OSError:
        return False


def _is_mode_active(mode: str) -> bool:
    """Check if the given mode currently has an active stream / connection."""
    data = load_modes()
    spec = (data.get("modes") or {}).get(mode)
    if not spec:
        return False
    detect = spec.get("detect")
    if not detect:
        return False
    if detect.get("bt_connected"):
        return check_bt_audio_playing()
    # For process-based detection, require state_file to confirm playback
    # if one is configured; fall back to TCP connection check otherwise.
    if detect.get("process"):
        state_file = detect.get("state_file")
        if state_file:
            return _check_state_file(state_file)
        return _check_process_connections(detect["process"])
    return False


def detect_active_mode() -> str | None:
    """Return the mode that should be active based on stream detection."""
    data = load_modes()
    modes = data.get("modes") or {}
    current = read_last_mode_valid()

    if current and _is_mode_active(current):
        return current

    for name in modes:
        if name == current:
            continue
        if _is_mode_active(name):
            return name

    return None


# --- Auto-mode background loop ---


async def auto_mode_loop() -> None:
    """Background task: if current mode is idle and another is streaming, switch."""
    while True:
        await asyncio.sleep(5)
        if not read_auto_mode():
            continue
        try:
            detected = await asyncio.get_event_loop().run_in_executor(None, detect_active_mode)
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


# --- Routes ---


@router.get("/auto-mode")
def get_auto_mode() -> dict[str, bool]:
    return {"enabled": read_auto_mode()}


@router.post("/auto-mode")
def post_auto_mode(body: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(body.get("enabled", False))
    write_auto_mode(enabled)
    return {"ok": True, "enabled": enabled}


@router.post("/mode/{mode}")
def post_mode(mode: str) -> dict[str, str]:
    try:
        apply_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"mode": mode, "ok": "true"}
