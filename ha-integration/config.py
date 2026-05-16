"""Configuration loading and shared constants."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
log = logging.getLogger("piradio-ha")

DEFAULT_CONFIG_PATHS = [
    Path(os.environ.get("PIRADIO_HA_CONFIG", "")),
    Path("/etc/pi-audio-controller/piradio-ha.yaml"),
    Path("/etc/pi-audio-controller/softwarebuttond.yaml"),
    Path(__file__).resolve().parent.parent / "config" / "piradio-ha.yaml",
]

CONTROLLER_URL = os.environ.get("PIRADIO_CONTROLLER_URL", "http://127.0.0.1:8765")

CURRENT_MODE_FILE = Path(
    os.environ.get("PI_AUDIO_STATE", "/var/lib/pi-audio-controller/current-mode")
)

SPOTIFY_STATE_FILE = Path(
    os.environ.get("SPOTIFY_PLAYBACK_STATE", "/var/lib/pi-audio-controller/spotify-playback-state")
)

AUTO_MODE_FILE = Path(
    os.environ.get("PI_AUDIO_AUTO_MODE", "/var/lib/pi-audio-controller/auto-mode")
)

VALID_MODES = ("airplay", "spotify", "bluetooth")


def load_config() -> dict[str, Any]:
    for p in DEFAULT_CONFIG_PATHS:
        if p and p.is_file():
            with open(p, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            log.info("Loaded config from %s", p)
            return data
    log.warning("No config file found; using defaults.")
    return {}
