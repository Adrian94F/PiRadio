"""Main daemon — orchestrates monitors, MQTT, and reconciliation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dbus_next.aio import MessageBus
from dbus_next import BusType, Message

from .config import AUTO_MODE_FILE, CURRENT_MODE_FILE
from .detection import (
    call_controller,
    check_bt_connected,
    check_process_connections,
    read_auto_mode,
    read_current_mode,
)
from .mqtt_publisher import MQTTPublisher
from .state import DaemonState
from .bluetooth_monitor import BluetoothMonitor
from .mpris_monitor import MPRISMonitor
from .shairport_monitor import ShairportMonitor
from .spotify_monitor import SpotifyFileMonitor

try:
    from asyncinotify import Inotify, Mask as InotifyMask
    HAS_INOTIFY = True
except ImportError:
    HAS_INOTIFY = False

log = logging.getLogger(__name__)


class PiRadioHADaemon:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._state = DaemonState()
        self._mqtt = MQTTPublisher(config.get("mqtt", {}))
        self._mqtt._on_mode_command = self._handle_mode_command
        self._mqtt._on_auto_mode_command = self._handle_auto_mode_command
        self._bus: MessageBus | None = None
        self._reconcile_interval: float = config.get("reconcile_interval", 10.0)
        self._running = False
        self._spotify_file_monitor: SpotifyFileMonitor | None = None
        self._mode_watch_task: asyncio.Task | None = None
        self._last_mode: str | None = None
        self._last_auto_mode: bool | None = None

    def _handle_mode_command(self, mode: str) -> None:
        log.info("HA requested mode switch to: %s", mode)
        result = call_controller("POST", f"/api/mode/{mode}")
        if result and result.get("ok"):
            log.info("Mode switched to %s", mode)
            self._mqtt.publish_mode(mode)
        else:
            log.error("Failed to switch mode to %s", mode)

    def _handle_auto_mode_command(self, enabled: bool) -> None:
        log.info("HA requested auto_mode=%s", enabled)
        result = call_controller("POST", "/api/auto-mode", {"enabled": enabled})
        if result and result.get("ok"):
            log.info("Auto mode set to %s", enabled)
            self._mqtt.publish_auto_mode(enabled)
        else:
            log.error("Failed to set auto_mode=%s", enabled)

    async def run(self) -> None:
        self._running = True
        self._mqtt.connect()
        await asyncio.sleep(1)

        self._spotify_file_monitor = SpotifyFileMonitor(self._state.spotify, self._publish_state)
        await self._spotify_file_monitor.setup()

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            log.error("Cannot connect to system D-Bus: %s", e)
            log.info("Falling back to polling-only mode.")
            self._bus = None

        if self._bus:
            await self._add_match_rules()
            mpris = MPRISMonitor(self._bus, self._state.spotify, self._publish_state)
            shairport = ShairportMonitor(self._bus, self._state.airplay, self._publish_state)
            bt = BluetoothMonitor(self._bus, self._state.bluetooth, self._publish_state)
            await mpris.setup()
            await shairport.setup()
            await bt.setup()
            log.info("D-Bus monitors active.")

        if HAS_INOTIFY:
            self._mode_watch_task = asyncio.ensure_future(self._watch_mode_file())

        self._publish_state()
        self._publish_mode_and_auto()

        while self._running:
            await asyncio.sleep(self._reconcile_interval)
            self._reconcile()

    def _publish_mode_and_auto(self) -> None:
        mode = read_current_mode()
        if mode and mode != self._last_mode:
            self._last_mode = mode
            self._mqtt.publish_mode(mode)
        auto = read_auto_mode()
        if auto != self._last_auto_mode:
            self._last_auto_mode = auto
            self._mqtt.publish_auto_mode(auto)

    async def _watch_mode_file(self) -> None:
        parent = CURRENT_MODE_FILE.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            with Inotify() as inotify:
                inotify.add_watch(parent, InotifyMask.CLOSE_WRITE | InotifyMask.MOVED_TO)
                async for event in inotify:
                    name = str(event.name) if event.name else ""
                    if name in (CURRENT_MODE_FILE.name, AUTO_MODE_FILE.name):
                        self._publish_mode_and_auto()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Mode file watch error: %s; relying on polling.", e)

    async def _add_match_rules(self) -> None:
        assert self._bus
        rules = [
            "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
            "type='signal',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesAdded'",
            "type='signal',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesRemoved'",
            "type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged'",
        ]
        for rule in rules:
            msg = Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            )
            await self._bus.call(msg)

    def _reconcile(self) -> None:
        changed = False

        if self._spotify_file_monitor:
            self._spotify_file_monitor.read_state()
        elif not self._state.spotify.dbus_available:
            if self._state.spotify.set_playing(check_process_connections("librespot")):
                changed = True

        if not self._state.airplay.dbus_available:
            if self._state.airplay.set_playing(check_process_connections("shairport-sync")):
                changed = True

        if not self._state.bluetooth.dbus_available:
            if self._state.bluetooth.set_playing(check_bt_connected()):
                changed = True

        if changed:
            self._publish_state()

        self._publish_mode_and_auto()

    def _publish_state(self) -> None:
        log.debug(
            "State: spotify=%s airplay=%s bt=%s active=%s",
            self._state.spotify.playing,
            self._state.airplay.playing,
            self._state.bluetooth.playing,
            self._state.active_source,
        )
        self._mqtt.publish_state(self._state)

    def stop(self) -> None:
        self._running = False
        if self._spotify_file_monitor:
            self._spotify_file_monitor.stop()
        if self._mode_watch_task and not self._mode_watch_task.done():
            self._mode_watch_task.cancel()
        self._mqtt.stop()
