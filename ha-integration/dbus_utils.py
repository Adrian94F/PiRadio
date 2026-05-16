"""Shared D-Bus constants and helper utilities."""

from __future__ import annotations

from typing import Any

from dbus_next.aio import MessageBus
from dbus_next import Message, MessageType, Variant

MPRIS_IFACE = "org.mpris.MediaPlayer2.Player"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
BLUEZ_SERVICE = "org.bluez"
BLUEZ_TRANSPORT_IFACE = "org.bluez.MediaTransport1"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
SHAIRPORT_SERVICE = "org.gnome.ShairportSync"
SHAIRPORT_PATH = "/org/gnome/ShairportSync"
SHAIRPORT_REMOTE_IFACE = "org.gnome.ShairportSync.RemoteControl"
SHAIRPORT_DIAG_IFACE = "org.gnome.ShairportSync.Diagnostics"


async def get_property(bus: MessageBus, service: str, path: str, iface: str, prop: str) -> Any:
    """Get a single D-Bus property."""
    msg = Message(
        destination=service,
        path=path,
        interface=PROPERTIES_IFACE,
        member="Get",
        signature="ss",
        body=[iface, prop],
    )
    reply = await bus.call(msg)
    if reply.message_type == MessageType.ERROR:
        return None
    variant = reply.body[0] if reply.body else None
    if isinstance(variant, Variant):
        return variant.value
    return variant
