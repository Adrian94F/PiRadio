"""Mode management — apply modes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .config import STATE_FILE, load_modes
from .system import run_systemctl

router = APIRouter(prefix="/api", tags=["modes"])


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


# --- Routes ---


@router.post("/mode/{mode}")
def post_mode(mode: str) -> dict[str, str]:
    try:
        apply_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"mode": mode, "ok": "true"}
