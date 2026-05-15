#!/usr/bin/env python3
"""
softwarebuttond — D-Bus → MQTT bridge for Pi Audio Controller.

Monitors playback state of Spotify (MPRIS2), AirPlay (shairport-sync D-Bus),
and Bluetooth A2DP (BlueZ MediaTransport1) via D-Bus subscriptions on the
system bus.  Publishes binary_sensor and sensor entities to Home Assistant
via MQTT Discovery.

Event-driven with a periodic reconciliation fallback (for sources whose
D-Bus interface may not be available).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from dbus_next.aio import MessageBus
from dbus_next import BusType, Message, MessageType, Variant
import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATHS = [
    Path(os.environ.get("SOFTWAREBUTTOND_CONFIG", "")),
    Path("/etc/pi-audio-controller/softwarebuttond.yaml"),
    Path(__file__).resolve().parent.parent / "config" / "softwarebuttond.yaml",
]

CURRENT_MODE_FILE = Path(
    os.environ.get(
        "PI_AUDIO_STATE",
        "/var/lib/pi-audio-controller/current-mode",
    )
)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
log = logging.getLogger("softwarebuttond")


def load_config() -> dict[str, Any]:
    for p in DEFAULT_CONFIG_PATHS:
        if p and p.is_file():
            with open(p, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            log.info("Loaded config from %s", p)
            return data
    log.warning("No config file found; using defaults.")
    return {}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

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
    def __init__(self) -> None:
        self.spotify = SourceState("spotify")
        self.airplay = SourceState("airplay")
        self.bluetooth = SourceState("bluetooth")
        self.sources = [self.spotify, self.airplay, self.bluetooth]

    @property
    def any_playing(self) -> bool:
        return any(s.playing for s in self.sources)

    @property
    def active_source(self) -> str:
        for s in self.sources:
            if s.playing:
                return s.name
        return "idle"


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------

DISCOVERY_PREFIX = "homeassistant"
STATE_PREFIX = "pi_audio"
DEVICE_INFO = {
    "identifiers": ["pi_audio_controller"],
    "name": "Pi Audio Controller",
    "manufacturer": "DIY",
    "model": "Raspberry Pi",
}


class MQTTPublisher:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self._host = cfg.get("host", "127.0.0.1")
        self._port = int(cfg.get("port", 1883))
        self._user = cfg.get("username")
        self._pass = cfg.get("password")
        self._client = mqtt.Client(
            client_id="softwarebuttond",
            protocol=mqtt.MQTTv311,
        )
        if self._user:
            self._client.username_pw_set(self._user, self._pass)
        self._client.will_set(
            f"{STATE_PREFIX}/status", "offline", qos=1, retain=True,
        )
        self._connected = False

    def connect(self) -> None:
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.connect_async(self._host, self._port)
        self._client.loop_start()

    def stop(self) -> None:
        self._publish(f"{STATE_PREFIX}/status", "offline", retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        log.info("MQTT connected (rc=%d)", rc)
        self._connected = True
        self._publish(f"{STATE_PREFIX}/status", "online", retain=True)
        self._send_discovery()

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        log.warning("MQTT disconnected (rc=%d)", rc)
        self._connected = False

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=1, retain=retain)

    def _send_discovery(self) -> None:
        """Publish HA MQTT Discovery configs for all entities."""
        availability = {
            "availability_topic": f"{STATE_PREFIX}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

        # Binary sensors per source
        for source in ("spotify", "airplay", "bluetooth", "any"):
            nice = {"spotify": "Spotify", "airplay": "AirPlay", "bluetooth": "Bluetooth", "any": "Any Audio"}[source]
            config = {
                "name": f"{nice} Playing",
                "unique_id": f"pi_audio_{source}_playing",
                "state_topic": f"{STATE_PREFIX}/{source}/state",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "sound",
                "device": DEVICE_INFO,
                **availability,
            }
            topic = f"{DISCOVERY_PREFIX}/binary_sensor/pi_audio/{source}/config"
            self._publish(topic, json.dumps(config), retain=True)

        # Sensor: active source
        config = {
            "name": "Active Audio Source",
            "unique_id": "pi_audio_active_source",
            "state_topic": f"{STATE_PREFIX}/source/state",
            "icon": "mdi:speaker",
            "device": DEVICE_INFO,
            **availability,
        }
        topic = f"{DISCOVERY_PREFIX}/sensor/pi_audio/source/config"
        self._publish(topic, json.dumps(config), retain=True)
        log.info("MQTT Discovery configs published.")

    def publish_state(self, state: DaemonState) -> None:
        if not self._connected:
            return
        for src in state.sources:
            payload = "ON" if src.playing else "OFF"
            self._publish(f"{STATE_PREFIX}/{src.name}/state", payload, retain=True)
        self._publish(
            f"{STATE_PREFIX}/any/state",
            "ON" if state.any_playing else "OFF",
            retain=True,
        )
        self._publish(
            f"{STATE_PREFIX}/source/state",
            state.active_source,
            retain=True,
        )


# ---------------------------------------------------------------------------
# D-Bus monitors
# ---------------------------------------------------------------------------

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


async def _get_property(bus: MessageBus, service: str, path: str, iface: str, prop: str) -> Any:
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


class MPRISMonitor:
    """Monitor MPRIS2 players on the bus for Spotify/raspotify playback."""

    KNOWN_PLAYERS = ("raspotify", "librespot", "spotify", "Spotify")

    def __init__(self, bus: MessageBus, state: SourceState, on_change: Any) -> None:
        self._bus = bus
        self._state = state
        self._on_change = on_change
        self._tracked_names: set[str] = set()

    async def setup(self) -> None:
        # Subscribe to PropertiesChanged for MPRIS player interface
        self._bus.add_message_handler(self._on_message)
        # Find already-registered MPRIS players
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
        names = reply.body[0]
        for name in names:
            if name.startswith("org.mpris.MediaPlayer2."):
                player_id = name.split(".", 3)[-1]
                if self._is_spotify_player(player_id):
                    self._tracked_names.add(name)
                    await self._check_player_status(name)

    def _is_spotify_player(self, player_id: str) -> bool:
        lower = player_id.lower()
        for known in self.KNOWN_PLAYERS:
            if known.lower() in lower:
                return True
        return False

    async def _check_player_status(self, service_name: str) -> None:
        status = await _get_property(
            self._bus, service_name, MPRIS_PATH, MPRIS_IFACE, "PlaybackStatus",
        )
        playing = (status == "Playing") if status else False
        if self._state.set_playing(playing):
            self._state.dbus_available = True
            self._on_change()

    def _on_message(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL:
            return False
        # NameOwnerChanged — track player appearance/disappearance
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

        # PropertiesChanged on MPRIS Player interface
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
                # Check sender is a tracked player
                sender = msg.sender
                if sender and any(sender == n or n in self._tracked_names for n in [sender]):
                    playing = (status == "Playing")
                    self._state.dbus_available = True
                    if self._state.set_playing(playing):
                        self._on_change()
                else:
                    # Even unknown sender on MPRIS path — check if it's spotify-like
                    playing = (status == "Playing")
                    self._state.dbus_available = True
                    if self._state.set_playing(playing):
                        self._on_change()
            return False
        return False


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
        # Try RemoteControl.PlayerState or Diagnostics
        status = await _get_property(
            self._bus, SHAIRPORT_SERVICE, SHAIRPORT_PATH,
            SHAIRPORT_REMOTE_IFACE, "PlayerState",
        )
        if status is not None:
            self._state.dbus_available = True
            # PlayerState: 0=not available, 1=stopped/paused, 2=playing
            playing = (status == 2) if isinstance(status, int) else False
            if self._state.set_playing(playing):
                self._on_change()
            return
        # Alternative: check if service name exists on bus
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

        # NameOwnerChanged for shairport-sync disappearing
        if (
            msg.interface == "org.freedesktop.DBus"
            and msg.member == "NameOwnerChanged"
            and msg.body
            and msg.body[0] == SHAIRPORT_SERVICE
        ):
            _name, _old, new_owner = msg.body
            if not new_owner:
                # Service gone — not playing
                if self._state.set_playing(False):
                    self._on_change()
            else:
                self._state.dbus_available = True
                asyncio.ensure_future(self._check_initial_state())
            return False

        # PropertiesChanged from shairport-sync
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
            # Also handle shairport-sync "Active" property on Diagnostics
            if iface_name == SHAIRPORT_DIAG_IFACE and "Active" in changed_props:
                val = changed_props["Active"]
                active = val.value if isinstance(val, Variant) else val
                self._state.dbus_available = True
                if self._state.set_playing(bool(active)):
                    self._on_change()
                return False
        return False


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
        objects = reply.body[0]  # dict[str, dict[str, dict[str, Variant]]]
        for path, ifaces in objects.items():
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

        # InterfacesAdded — new transport
        if (
            msg.interface == OBJECT_MANAGER_IFACE
            and msg.member == "InterfacesAdded"
            and msg.body
        ):
            path = msg.body[0]
            ifaces = msg.body[1] if len(msg.body) > 1 else {}
            if BLUEZ_TRANSPORT_IFACE in ifaces:
                props = ifaces[BLUEZ_TRANSPORT_IFACE]
                state_val = props.get("State")
                s = "idle"
                if state_val:
                    s = state_val.value if isinstance(state_val, Variant) else state_val
                self._transports[path] = s
                self._update_playing_state()
            return False

        # InterfacesRemoved — transport gone
        if (
            msg.interface == OBJECT_MANAGER_IFACE
            and msg.member == "InterfacesRemoved"
            and msg.body
        ):
            path = msg.body[0]
            removed_ifaces = msg.body[1] if len(msg.body) > 1 else []
            if BLUEZ_TRANSPORT_IFACE in removed_ifaces:
                self._transports.pop(path, None)
                self._update_playing_state()
            return False

        # PropertiesChanged on a transport path
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


# ---------------------------------------------------------------------------
# Fallback detection (for sources without D-Bus)
# ---------------------------------------------------------------------------

def _check_process_connections(process_name: str) -> bool:
    """True if *process_name* has ESTABLISHED TCP connections."""
    ss = shutil.which("ss")
    if not ss:
        return False
    try:
        proc = subprocess.run(
            [ss, "-tnpH", "state", "established"],
            capture_output=True, text=True, timeout=5,
        )
        return process_name in (proc.stdout or "")
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_bt_connected() -> bool:
    """True if any Bluetooth device is connected (fallback)."""
    exe = shutil.which("bluetoothctl")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe],
            input="devices Connected\nquit\n",
            capture_output=True, text=True, timeout=10,
        )
        out = proc.stdout or ""
        # Look for Device lines
        import re
        return bool(re.search(r"^Device\s+[0-9A-Fa-f:]{17}", out, re.M))
    except (subprocess.TimeoutExpired, OSError):
        return False


def read_current_mode() -> str | None:
    try:
        return CURRENT_MODE_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Main daemon
# ---------------------------------------------------------------------------

class SoftwareButtonDaemon:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._state = DaemonState()
        self._mqtt = MQTTPublisher(config.get("mqtt", {}))
        self._bus: MessageBus | None = None
        self._reconcile_interval: float = config.get("reconcile_interval", 10.0)
        self._running = False

    async def run(self) -> None:
        self._running = True
        self._mqtt.connect()

        # Give MQTT a moment to connect
        await asyncio.sleep(1)

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            log.error("Cannot connect to system D-Bus: %s", e)
            log.info("Falling back to polling-only mode.")
            self._bus = None

        if self._bus:
            # Add match rules for PropertiesChanged signals we care about
            await self._add_match_rules()
            # Set up monitors
            mpris = MPRISMonitor(self._bus, self._state.spotify, self._publish_state)
            shairport = ShairportMonitor(self._bus, self._state.airplay, self._publish_state)
            bt = BluetoothMonitor(self._bus, self._state.bluetooth, self._publish_state)
            await mpris.setup()
            await shairport.setup()
            await bt.setup()
            log.info("D-Bus monitors active.")

        # Initial publish
        self._publish_state()

        # Reconciliation loop
        while self._running:
            await asyncio.sleep(self._reconcile_interval)
            self._reconcile()

    async def _add_match_rules(self) -> None:
        """Add D-Bus match rules so we receive relevant signals."""
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
        """Periodic fallback check for sources without D-Bus signals."""
        changed = False

        # Spotify fallback
        if not self._state.spotify.dbus_available:
            playing = _check_process_connections("librespot")
            if self._state.spotify.set_playing(playing):
                changed = True

        # AirPlay fallback
        if not self._state.airplay.dbus_available:
            playing = _check_process_connections("shairport-sync")
            if self._state.airplay.set_playing(playing):
                changed = True

        # Bluetooth fallback
        if not self._state.bluetooth.dbus_available:
            playing = _check_bt_connected()
            if self._state.bluetooth.set_playing(playing):
                changed = True

        if changed:
            self._publish_state()

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
        self._mqtt.stop()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=LOG_FORMAT,
    )
    config = load_config()
    daemon = SoftwareButtonDaemon(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig: int, frame: Any) -> None:
        log.info("Received signal %d, shutting down…", sig)
        daemon.stop()
        loop.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(daemon.run())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        daemon.stop()
        loop.close()
    log.info("softwarebuttond stopped.")


if __name__ == "__main__":
    main()
