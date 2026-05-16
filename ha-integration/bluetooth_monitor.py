"""BlueZ D-Bus monitor for Bluetooth A2DP streaming state."""

from __future__ import annotations

import logging
from typing import Any

from dbus_next.aio import MessageBus
from dbus_next import Message, MessageType, Variant

from .dbus_utils import (
    BLUEZ_SERVICE,
    BLUEZ_TRANSPORT_IFACE,
    OBJECT_MANAGER_IFACE,
    PROPERTIES_IFACE,
)
from .state import SourceState

log = logging.getLogger(__name__)


class BluetoothMonitor:
    """Monitor BlueZ MediaTransport1 for A2DP audio streaming state."""

    def __init__(self, bus: MessageBus, state: SourceState, on_change: Any) -> None:
        self._bus = bus
        self._state = state
        self._on_change = on_change
        self._transports: dict[str, str] = {}  # path -> state

    async def setup(self) -> None:
        self._bus.add_message_handler(self._on_message)
        await self._scan_existing_transports()

    async def _scan_existing_transports(self) -> None:
        """Use ObjectManager to find existing MediaTransport1 objects."""
        msg = Message(
            destination=BLUEZ_SERVICE,
            path="/",
            interface=OBJECT_MANAGER_IFACE,
            member="GetManagedObjects",
        )
        reply = await self._bus.call(msg)
        if reply.message_type == MessageType.ERROR or not reply.body:
            return
        for path, ifaces in reply.body[0].items():
            if BLUEZ_TRANSPORT_IFACE in ifaces:
                props = ifaces[BLUEZ_TRANSPORT_IFACE]
                state_val = props.get("State")
                if state_val:
                    s = state_val.value if isinstance(state_val, Variant) else state_val
                    self._transports[path] = s
        self._update_playing_state()

    def _update_playing_state(self) -> None:
        any_active = any(s == "active" for s in self._transports.values())
        self._state.dbus_available = True
        if self._state.set_playing(any_active):
            self._on_change()

    def _on_message(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL:
            return False

        if msg.interface == OBJECT_MANAGER_IFACE and msg.member == "InterfacesAdded" and msg.body:
            path = msg.body[0]
            ifaces = msg.body[1] if len(msg.body) > 1 else {}
            if BLUEZ_TRANSPORT_IFACE in ifaces:
                props = ifaces[BLUEZ_TRANSPORT_IFACE]
                state_val = props.get("State")
                s = (state_val.value if isinstance(state_val, Variant) else state_val) if state_val else "idle"
                self._transports[path] = s
                self._update_playing_state()
            return False

        if msg.interface == OBJECT_MANAGER_IFACE and msg.member == "InterfacesRemoved" and msg.body:
            path = msg.body[0]
            removed_ifaces = msg.body[1] if len(msg.body) > 1 else []
            if BLUEZ_TRANSPORT_IFACE in removed_ifaces:
                self._transports.pop(path, None)
                self._update_playing_state()
            return False

        if (
            msg.interface == PROPERTIES_IFACE
            and msg.member == "PropertiesChanged"
            and msg.body
        ):
            iface_name = msg.body[0]
            changed_props = msg.body[1] if len(msg.body) > 1 else {}
            if iface_name == BLUEZ_TRANSPORT_IFACE and "State" in changed_props:
                path = msg.path or ""
                val = changed_props["State"]
                s = val.value if isinstance(val, Variant) else val
                self._transports[path] = s
                self._update_playing_state()
            return False

        return False
