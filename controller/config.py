"""Shared constants and configuration loading."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIRS = [
    Path(os.environ.get("PI_AUDIO_CONFIG", "/etc/pi-audio-controller")),
    Path(__file__).resolve().parent.parent / "config",
]

STATE_FILE = Path(
    os.environ.get("PI_AUDIO_STATE", "/var/lib/pi-audio-controller/current-mode")
)

AUDIO_STATE_FILE = Path(
    os.environ.get("PI_AUDIO_OUTPUT_STATE", "/var/lib/pi-audio-controller/audio-output.json")
)

ASOUND_PATH = Path(os.environ.get("PI_AUDIO_ASOUND", "/etc/asound.conf"))
ASOUND_MARKER = "# Managed by pi-audio-controller"

AUTO_MODE_FILE = Path(
    os.environ.get("PI_AUDIO_AUTO_MODE", "/var/lib/pi-audio-controller/auto-mode")
)

DEVICE_NAME_FILE = Path(
    os.environ.get("PI_AUDIO_DEVICE_NAME", "/var/lib/pi-audio-controller/device-name")
)

SHAIRPORT_CONF = Path("/etc/shairport-sync.conf")
RASPOTIFY_CONF = Path("/etc/raspotify/conf")
STATIC_DIR = Path(__file__).resolve().parent / "static"

APLAY_LINE = re.compile(r"^card\s+(\d+):\s*(.+?),\s*device\s+(\d+):\s*(.+)$")
BT_CONTROLLER = re.compile(r"^Controller\s+([0-9A-Fa-f:]+)", re.I)
BT_DEVICE_LINE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.*)$")


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
