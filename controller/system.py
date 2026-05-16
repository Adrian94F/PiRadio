"""Systemd helpers — run systemctl commands and check unit state."""

from __future__ import annotations

import shutil
import subprocess


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
    code, out, _ = run_systemctl(["is-active", unit])
    if code != 0 and not out:
        return "unknown"
    return out.strip() or "unknown"
