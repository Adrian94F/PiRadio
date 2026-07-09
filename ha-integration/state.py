"""Playback state tracking for audio sources."""

from __future__ import annotations


class SourceState:
    """Holds playback state for a single audio source."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.playing: bool = False
        self.dbus_available: bool = False

    def set_playing(self, playing: bool) -> bool:
        """Update state, return True if changed."""
        if self.playing == playing:
            return False
        self.playing = playing
        return True


class DaemonState:
    """Aggregate playback state across all sources."""

    def __init__(self) -> None:
        self.airplay = SourceState("airplay")
        self.sources = [self.airplay]

    @property
    def any_playing(self) -> bool:
        return any(s.playing for s in self.sources)

    @property
    def active_source(self) -> str:
        for s in self.sources:
            if s.playing:
                return s.name
        return "idle"
