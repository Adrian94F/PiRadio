"""Spotify state file monitor using inotify (librespot --onevent writes state)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import SPOTIFY_STATE_FILE
from .state import SourceState

try:
    from asyncinotify import Inotify, Mask as InotifyMask
    HAS_INOTIFY = True
except ImportError:
    HAS_INOTIFY = False

log = logging.getLogger(__name__)


class SpotifyFileMonitor:
    """Monitor librespot --onevent state file for Spotify playback.

    Librespot (raspotify) writes 'playing', 'paused', or 'stopped' to the
    state file via the onevent script.  This monitor watches the file with
    inotify (if available) for instant detection.
    """

    def __init__(self, state: SourceState, on_change: Any) -> None:
        self._state = state
        self._on_change = on_change
        self._watch_task: asyncio.Task | None = None

    def read_state(self) -> None:
        """Read current state from file (used by reconcile and initial check)."""
        try:
            text = SPOTIFY_STATE_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return
        playing = (text == "playing")
        if self._state.set_playing(playing):
            self._on_change()

    async def setup(self) -> None:
        self.read_state()
        if HAS_INOTIFY:
            self._watch_task = asyncio.ensure_future(self._watch_loop())
        else:
            log.warning("asyncinotify not available; Spotify detection will use polling only.")

    async def _watch_loop(self) -> None:
        parent = SPOTIFY_STATE_FILE.parent
        parent.mkdir(parents=True, exist_ok=True)
        if not SPOTIFY_STATE_FILE.exists():
            SPOTIFY_STATE_FILE.write_text("stopped\n", encoding="utf-8")
        try:
            with Inotify() as inotify:
                inotify.add_watch(parent, InotifyMask.CLOSE_WRITE | InotifyMask.MOVED_TO)
                async for event in inotify:
                    if event.name and str(event.name) == SPOTIFY_STATE_FILE.name:
                        self.read_state()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Inotify watch error: %s; falling back to polling.", e)

    def stop(self) -> None:
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
