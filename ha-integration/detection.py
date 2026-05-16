"""Fallback detection (process connections, BT) and controller API calls."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

from .config import AUTO_MODE_FILE, CONTROLLER_URL, CURRENT_MODE_FILE

log = logging.getLogger(__name__)


def check_process_connections(process_name: str) -> bool:
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


def check_bt_connected() -> bool:
    """True if any Bluetooth device is connected (fallback)."""
    exe = shutil.which("bluetoothctl")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe],
            input="devices Connected\nquit\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(re.search(r"^Device\s+[0-9A-Fa-f:]{17}", proc.stdout or "", re.M))
    except (subprocess.TimeoutExpired, OSError):
        return False


def read_current_mode() -> str | None:
    try:
        return CURRENT_MODE_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def read_auto_mode() -> bool:
    try:
        return AUTO_MODE_FILE.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def call_controller(method: str, path: str, body: dict | None = None) -> dict | None:
    """Call the Pi Audio Controller HTTP API."""
    url = f"{CONTROLLER_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log.error("Controller API %s %s failed: %s", method, path, e)
        return None
