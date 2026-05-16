"""MQTT publisher — Home Assistant Discovery and state publishing."""

from __future__ import annotations

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

from .config import VALID_MODES
from .state import DaemonState

log = logging.getLogger(__name__)

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
        self._client = mqtt.Client(client_id="piradio-ha", protocol=mqtt.MQTTv311)
        if self._user:
            self._client.username_pw_set(self._user, self._pass)
        self._client.will_set(f"{STATE_PREFIX}/status", "offline", qos=1, retain=True)
        self._connected = False
        self._on_mode_command: Any = None
        self._on_auto_mode_command: Any = None

    def connect(self) -> None:
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
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
        client.subscribe(f"{STATE_PREFIX}/mode/set", qos=1)
        client.subscribe(f"{STATE_PREFIX}/auto_mode/set", qos=1)

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        log.warning("MQTT disconnected (rc=%d)", rc)
        self._connected = False

    def _on_message(self, client: Any, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        log.info("MQTT command: %s = %s", topic, payload)

        if topic == f"{STATE_PREFIX}/mode/set":
            if payload in VALID_MODES and self._on_mode_command:
                self._on_mode_command(payload)
        elif topic == f"{STATE_PREFIX}/auto_mode/set":
            if payload in ("ON", "OFF") and self._on_auto_mode_command:
                self._on_auto_mode_command(payload == "ON")

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=1, retain=retain)

    def _send_discovery(self) -> None:
        """Publish HA MQTT Discovery configs for all entities."""
        availability = {
            "availability_topic": f"{STATE_PREFIX}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

        for source in ("spotify", "airplay", "bluetooth", "any"):
            nice = {
                "spotify": "Spotify",
                "airplay": "AirPlay",
                "bluetooth": "Bluetooth",
                "any": "Any Audio",
            }[source]
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
        self._publish(
            f"{DISCOVERY_PREFIX}/sensor/pi_audio/source/config",
            json.dumps(config),
            retain=True,
        )

        # Select: mode switching
        config = {
            "name": "Audio Mode",
            "unique_id": "pi_audio_mode",
            "state_topic": f"{STATE_PREFIX}/mode/state",
            "command_topic": f"{STATE_PREFIX}/mode/set",
            "options": list(VALID_MODES),
            "icon": "mdi:tune-variant",
            "device": DEVICE_INFO,
            **availability,
        }
        self._publish(
            f"{DISCOVERY_PREFIX}/select/pi_audio/mode/config",
            json.dumps(config),
            retain=True,
        )

        # Switch: auto-mode
        config = {
            "name": "Auto Mode",
            "unique_id": "pi_audio_auto_mode",
            "state_topic": f"{STATE_PREFIX}/auto_mode/state",
            "command_topic": f"{STATE_PREFIX}/auto_mode/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:auto-fix",
            "device": DEVICE_INFO,
            **availability,
        }
        self._publish(
            f"{DISCOVERY_PREFIX}/switch/pi_audio/auto_mode/config",
            json.dumps(config),
            retain=True,
        )

        log.info("MQTT Discovery configs published.")

    def publish_state(self, state: DaemonState) -> None:
        if not self._connected:
            return
        for src in state.sources:
            self._publish(f"{STATE_PREFIX}/{src.name}/state", "ON" if src.playing else "OFF", retain=True)
        self._publish(f"{STATE_PREFIX}/any/state", "ON" if state.any_playing else "OFF", retain=True)
        self._publish(f"{STATE_PREFIX}/source/state", state.active_source, retain=True)

    def publish_mode(self, mode: str | None) -> None:
        if not self._connected or not mode:
            return
        self._publish(f"{STATE_PREFIX}/mode/state", mode, retain=True)

    def publish_auto_mode(self, enabled: bool) -> None:
        if not self._connected:
            return
        self._publish(f"{STATE_PREFIX}/auto_mode/state", "ON" if enabled else "OFF", retain=True)
