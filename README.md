# Pi Audio Controller

Small web UI and systemd wiring for a Raspberry Pi used as a **local network speaker** via **AirPlay 2** (`shairport-sync`). The UI lets you pick the ALSA output device and set the speaker name.

## Quick install

On the Pi, clone or copy this repo and run:

```bash
sudo ./setup/install.sh
```

The installer offers an interactive ALSA playback-device selection when run from a terminal. In non-interactive runs, the system's current default is left unchanged; you can select a device later in the web UI.

Then open `http://<pi-ip>:8765` on your LAN.

## Update after `git pull`

From the repo on the Pi (same paths as install):

```bash
cd /path/to/pi-audio-controller   # your clone
git pull
sudo ./setup/update.sh
```

Or pull inside the script:

```bash
sudo ./setup/update.sh --pull
```

Use **`--deps`** to upgrade Python packages to match `requirements.txt` (otherwise dependencies are only ensured to be installed). Existing **`/etc/pi-audio-controller/modes.yaml`** is never overwritten.

Configuration:

- **Modes and unit names:** `/etc/pi-audio-controller/modes.yaml` (installed once).
- **Last selected mode (for the UI):** `/var/lib/pi-audio-controller/current-mode`.
- **Default ALSA output (USB or built-in):** chosen in the web UI; written to **`/etc/asound.conf`** (previous file backed up to **`asound.conf.bak.pi-audio-*`** if it was not already managed) and saved under **`/var/lib/pi-audio-controller/audio-output.json`**. Services listed in **`alsa_restart_units`** in `modes.yaml` are restarted when they are active.
- **Shairport Sync:** `/etc/shairport-sync.conf`.

Requires **`alsa-utils`** (`aplay`) for device listing; the setup script installs it.

If a program ignores ALSA defaults, set its device explicitly (e.g. the **`alsa` section** in shairport-sync) to **`hw:CARD,DEV`** matching your selection.

## Development

```bash
cd controller
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PI_AUDIO_CONFIG=../config uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

The UI expects `systemctl` and `journalctl` (same as on the Pi).

## Home Assistant integration (piradio-ha)

An independent daemon **`piradio-ha`** bridges shairport-sync D-Bus playback events to MQTT so Home Assistant discovers the Pi as an audio device automatically.

### Architecture

```
                D-Bus
   ┌──────────────────────────────┐
   │                              │         MQTT
AirPlay ──► piradio-ha ───────────┼──────►──────► Home Assistant
                   ▲               │         (Discovery)
                   │               │
         ◄─── command topics ◄─────┼──── HA select
   └──────────────────────────────┘
              │
              ▼
    Pi Audio Controller API
    (POST /api/mode/*)
```

### What appears in Home Assistant (via MQTT Discovery)

| Entity | Type | Values |
|--------|------|--------|
| `binary_sensor.airplay_playing` | binary_sensor | ON / OFF |
| `binary_sensor.any_audio_playing` | binary_sensor | ON / OFF |
| `sensor.active_audio_source` | sensor | airplay / idle |
| `select.audio_mode` | select | airplay |

### Detection methods

| Source | D-Bus interface | Property |
|--------|----------------|----------|
| AirPlay (shairport-sync) | `org.gnome.ShairportSync.RemoteControl` | `PlayerState` (0/1/2) |

Falls back to TCP connection check (`ss`) if D-Bus is unavailable.

### Configuration

Edit `/etc/pi-audio-controller/piradio-ha.yaml`:

```yaml
mqtt:
  host: "127.0.0.1"
  port: 1883
  # username: ""
  # password: ""
reconcile_interval: 10
```

### Manual setup (if not using install.sh)

```bash
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto

# Daemon venv
sudo python3 -m venv /opt/pi-audio-controller/venv-piradio-ha
sudo /opt/pi-audio-controller/venv-piradio-ha/bin/pip install -r ha-integration/requirements.txt
sudo cp ha-integration/piradio-ha.py /opt/pi-audio-controller/
sudo cp config/piradio-ha.yaml /etc/pi-audio-controller/
sudo cp systemd/piradio-ha.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now piradio-ha
```

### Home Assistant side

1. Add the **MQTT integration** in HA (Settings → Devices & Services → Add → MQTT).
2. Point it at the Pi's IP (or `127.0.0.1` if HA runs on the same host), port 1883.
3. Entities appear automatically via MQTT Discovery — no manual YAML needed.
4. Use `select.audio_mode` to switch mode (currently `airplay`).

### Logs

```bash
journalctl -u piradio-ha -f
```
