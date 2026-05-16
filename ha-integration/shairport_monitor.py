"""Shairport-sync D-Bus monitor for AirPlay playback state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dbus_next.aio import MessageBus
from dbus_next import Message, MessageType, Variant

from .dbus_utils import (
    PROPERTIES_IFACE,
    SHAIRPORT_DIAG_IFACE,
    SHAIRPORT_PATH,
    SHAIRPORT_REMOTE_IFACE,
    SHAIRPORT_SERVICE,
    get_property,
)
from .state import SourceState

log = logging.getLogger(__name__)


class ShairportMonitor:
    """Monitor shairport-sync D-Bus interface for AirPlay playback state."""

    def __init__(self, bus: MessageBus, state: SourceState, on_change: Any) -> None:
        self._bus = bus
        self._state = state
        self._on_change = on_change

    async def setup(self) -> None:
        self._bus.add_message_handler(self._on_message)
        await self._check_initial_state()

    async def _check_initial_state(self) -> None:
        status = await get_property(
            self._bus, SHAIRPORT_SERVICE, SHAIRPORT_PATH,
            SHAIRPORT_REMOTE_IFACE, "PlayerState",
        )
        if status is not None:
            self._state.dbus_available = True
            playing = (status == 2) if isinstance(status, int) else False
            if self._state.set_playing(playing):
                self._on_change()
            return
        # Check if service name exists on bus
        msg = Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="NameHasOwner",
            signature="s",
            body=[SHAIRPORT_SERVICE],
        )
        reply = await self._bus.call(msg)
        if reply.body and reply.body[0]:
            self._state.dbus_available = True

    def _on_message(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL:
            return False

        if (
            msg.interface == "org.freedesktop.DBus"
            and msg.member == "NameOwnerChanged"
            and msg.body
            and msg.body[0] == SHAIRPORT_SERVICE
        ):
            _name, _old, new_owner = msg.body
            if not new_owner:
                if self._state.set_playing(False):
                    self._on_change()
            else:
                self._state.dbus_available = True
                asyncio.ensure_future(self._check_initial_state())
            return False

        if (
            msg.interface == PROPERTIES_IFACE
            and msg.member == "PropertiesChanged"
            and msg.body
        ):
            iface_name = msg.body[0]
            changed_props = msg.body[1] if len(msg.body) > 1 else {}

            if iface_name == SHAIRPORT_REMOTE_IFACE and "PlayerState" in changed_props:
                val = changed_props["PlayerState"]
                state_val = val.value if isinstance(val, Variant) else val
                playing = (state_val == 2) if isinstance(state_val, int) else False
                self._state.dbus_available = True
                if self._state.set_playing(playing):
                    self._on_change()
                return False

            if iface_name == SHAIRPORT_DIAG_IFACE and "Active" in changed_props:
                val = changed_props["Active"]
                active = val.value if isinstance(val, Variant) else val
                self._state.dbus_available = True
                if self._state.set_playing(bool(active)):
                    self._on_change()
                return False

        return False
