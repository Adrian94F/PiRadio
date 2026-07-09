"""ALSA audio output management — listing devices, writing asound.conf, selecting output."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import (
    APLAY_LINE,
    ASOUND_MARKER,
    ASOUND_PATH,
    AUDIO_STATE_FILE,
    load_modes,
)
from .system import run_systemctl, unit_active

router = APIRouter(prefix="/api", tags=["audio"])


class AudioSelectBody(BaseModel):
    id: str = Field(..., description='ALSA id from aplay -l, e.g. "1,0" for card 1 device 0')


def list_alsa_outputs() -> list[dict[str, Any]]:
    """Parse `aplay -l` playback devices."""
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
        out.append({
            "id": f"{card},{device}",
            "card": card,
            "device": device,
            "label": f"Card {card}: {card_name.strip()} — {dev_name.strip()}",
        })
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
    return ["shairport-sync.service"]


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
    """Write /etc/asound.conf routing the default PCM straight to the hardware.

    Single source (AirPlay) owns the card, so no dmix software mixer is needed.
    `plug` only converts rate/format/channels if the hardware can't take the
    stream natively — otherwise it is transparent (effectively direct output).
    """
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
        json.dumps({"id": valid["id"], "card": card, "device": device, "label": valid["label"]}, indent=2) + "\n",
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


# --- Routes ---


@router.get("/audio")
def get_audio() -> dict[str, Any]:
    return audio_status()


@router.post("/audio")
def post_audio(body: AudioSelectBody) -> dict[str, Any]:
    try:
        result = apply_audio_output(body.id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, **result}
