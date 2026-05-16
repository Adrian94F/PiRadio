"""MPRIS2 D-Bus monitor for Spotify/raspotify playback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dbus_next.aio import MessageBus
from dbus_next import Message, MessageType, Variant

from .dbus_utils import MPRIS_IFACE, MPRIS_PATH, PROPERTIES_IFACE, get_property
from .state import SourceState

log = logging.getLogger(__name__)

KNOWN_PLAYERS = ("raspotify", "librespot", "spotify", "Spotify")


class MPRISMonitor:
    """Monitor MPRIS2 players on the bus for Spotify/raspotify playback."""

    def __init__(self, bus: MessageBus, state: SourceState, on_change: Any) -> None:
        self._bus = bus
        self._state = state
        self._on_change = on_change
        self._tracked_names: set[str] = set()

    async def setup(self) -> None:
        self._bus.add_message_handler(self._on_message)
        await self._scan_existing_players()

    async def _scan_existing_players(self) -> None:
        msg = Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="ListNames",
        )
        reply = await self._bus.call(msg)
        if reply.message_type == MessageType.ERROR or not reply.body:
            return
        for name in reply.body[0]:
            if name.startswith("org.mpris.MediaPlayer2."):
                player_id = name.split(".", 3)[-1]
                if self._is_spotify_player(player_id):
                    self._tracked_names.add(name)
                    await self._check_player_status(name)

    def _is_spotify_player(self, player_id: str) -> bool:
        lower = player_id.lower()
        return any(known.lower() in lower for known in KNOWN_PLAYERS)

    async def _check_player_status(self, service_name: str) -> None:
        status = await get_property(self._bus, service_name, MPRIS_PATH, MPRIS_IFACE, "PlaybackStatus")
        playing = (status == "Playing") if status else False
        if self._state.set_playing(playing):
            self._state.dbus_available = True
            self._on_change()

    def _on_message(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL:
            return False

        if (
            msg.interface == "org.freedesktop.DBus"
            and msg.member == "NameOwnerChanged"
            and msg.body
        ):
            name, old_owner, new_owner = msg.body
            if name.startswith("org.mpris.MediaPlayer2."):
                player_id = name.split(".", 3)[-1]
                if self._is_spotify_player(player_id):
                    if new_owner:
                        self._tracked_names.add(name)
                        asyncio.ensure_future(self._check_player_status(name))
                    elif old_owner and not new_owner:
                        self._tracked_names.discard(name)
                        if self._state.set_playing(False):
                            self._on_change()
            return False

        if (
            msg.interface == PROPERTIES_IFACE
            and msg.member == "PropertiesChanged"
            and msg.path == MPRIS_PATH
            and msg.body
        ):
            iface_name = msg.body[0]
            changed_props = msg.body[1] if len(msg.body) > 1 else {}
            if iface_name == MPRIS_IFACE and "PlaybackStatus" in changed_props:
                status_variant = changed_props["PlaybackStatus"]
                status = status_variant.value if isinstance(status_variant, Variant) else status_variant
                playing = (status == "Playing")
                self._state.dbus_available = True
                if self._state.set_playing(playing):
                    self._on_change()
            return False

        return False
