"""WebSocket endpoint for tailing systemd journal logs."""

from __future__ import annotations

import asyncio
import json
import shutil

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["logs"])


async def _journal_lines(unit: str) -> asyncio.subprocess.Process:
    journalctl = shutil.which("journalctl")
    if not journalctl:
        raise RuntimeError("journalctl not found")
    return await asyncio.create_subprocess_exec(
        journalctl, "-u", unit, "-f", "-n", "80", "--no-pager", "-o", "short-iso",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


@router.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    try:
        payload = await ws.receive_text()
        msg = json.loads(payload)
        unit = msg.get("unit")
        if not unit or not isinstance(unit, str):
            await ws.send_text(json.dumps({"error": 'expected {"unit": "name.service"}'}))
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
