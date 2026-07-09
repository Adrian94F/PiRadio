"""Main daemon — orchestrates monitors, MQTT, and reconciliation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dbus_next.aio import MessageBus
from dbus_next import BusType, Message

from .config import CURRENT_MODE_FILE
from .detection import (
    call_controller,
    check_process_connections,
    read_current_mode,
)
from .mqtt_publisher import MQTTPublisher
from .state import DaemonState
from .shairport_monitor import ShairportMonitor

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
        self._bus: MessageBus | None = None
        self._reconcile_interval: float = config.get("reconcile_interval", 10.0)
        self._running = False
        self._mode_watch_task: asyncio.Task | None = None
        self._last_mode: str | None = None

    def _handle_mode_command(self, mode: str) -> None:
        log.info("HA requested mode switch to: %s", mode)
        result = call_controller("POST", f"/api/mode/{mode}")
        if result and result.get("ok"):
            log.info("Mode switched to %s", mode)
            self._mqtt.publish_mode(mode)
        else:
            log.error("Failed to switch mode to %s", mode)

    async def run(self) -> None:
        self._running = True
        self._mqtt.connect()
        await asyncio.sleep(1)

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            log.error("Cannot connect to system D-Bus: %s", e)
            log.info("Falling back to polling-only mode.")
            self._bus = None

        if self._bus:
            await self._add_match_rules()
            shairport = ShairportMonitor(self._bus, self._state.airplay, self._publish_state)
            await shairport.setup()
            log.info("D-Bus monitors active.")

        if HAS_INOTIFY:
            self._mode_watch_task = asyncio.ensure_future(self._watch_mode_file())

        self._publish_state()
        self._publish_mode()

        while self._running:
            await asyncio.sleep(self._reconcile_interval)
            self._reconcile()

    def _publish_mode(self) -> None:
        mode = read_current_mode()
        if mode and mode != self._last_mode:
            self._last_mode = mode
            self._mqtt.publish_mode(mode)

    async def _watch_mode_file(self) -> None:
        parent = CURRENT_MODE_FILE.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            with Inotify() as inotify:
                inotify.add_watch(parent, InotifyMask.CLOSE_WRITE | InotifyMask.MOVED_TO)
                async for event in inotify:
                    name = str(event.name) if event.name else ""
                    if name == CURRENT_MODE_FILE.name:
                        self._publish_mode()
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

        if not self._state.airplay.dbus_available:
            if self._state.airplay.set_playing(check_process_connections("shairport-sync")):
                changed = True

        if changed:
            self._publish_state()

        self._publish_mode()

    def _publish_state(self) -> None:
        log.debug(
            "State: airplay=%s active=%s",
            self._state.airplay.playing,
            self._state.active_source,
        )
        self._mqtt.publish_state(self._state)

    def stop(self) -> None:
        self._running = False
        if self._mode_watch_task and not self._mode_watch_task.done():
            self._mode_watch_task.cancel()
        self._mqtt.stop()
