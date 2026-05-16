# Pi Audio Controller

Small web UI and systemd wiring for a Raspberry Pi used as a **local network speaker**: AirPlay 2 (`shairport-sync`), Spotify Connect (`raspotify`), and **Bluetooth A2DP sink** (BlueALSA via **`bluez-alsa-utils`** on Debian 12 / recent Raspberry Pi OS — same daemon as the old `bluealsa` metapackage). Only one “sink mode” should own the speaker at a time; the UI switches modes by starting and stopping the right units.

## Quick install

On the Pi, clone or copy this repo and run:

```bash
sudo ./setup/install.sh
```

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

- **Modes and unit names:** `/etc/pi-audio-controller/modes.yaml` (installed once; edit to match your distro, e.g. `bluealsa@hci0.service` instead of `bluealsa.service`). Unquoted YAML keys like **`off`** are parsed as booleans, so the UI can show a broken **`false`** mode—use quoted keys (e.g. `"off":`) if you add an “all stopped” mode back.
- **Last selected mode (for the UI):** `/var/lib/pi-audio-controller/current-mode`.
- **Default ALSA output (USB or built-in):** chosen in the web UI; written to **`/etc/asound.conf`** (previous file backed up to **`asound.conf.bak.pi-audio-*`** if it was not already managed) and saved under **`/var/lib/pi-audio-controller/audio-output.json`**. Services listed in **`alsa_restart_units`** in `modes.yaml` are restarted when they are active.
- **Raspotify:** `/etc/default/raspotify`.
- **Shairport Sync:** `/etc/shairport-sync.conf`.

Requires **`alsa-utils`** (`aplay`) for device listing; the setup script installs it.

If a program ignores ALSA defaults, set its device explicitly (e.g. **`OPTIONS` / `--device`** in Raspotify, **`alsa` section** in shairport-sync) to **`hw:CARD,DEV`** matching your selection.

## Bluetooth notes

The **Settings** tab shows the adapter **friendly name**, **paired devices**, **currently connected** client(s), and controls to **make the Pi discoverable** for pairing (`bluetoothctl discoverable` / `pairable`). The controller runs as root so it can drive **`bluetoothctl`** and **`rfkill unblock bluetooth`**; **`bluez`** must be installed.

If the adapter is **off** or **rfkill-blocked**, the UI shows **Enable Bluetooth** (`POST /api/bluetooth/enable`: **`rfkill unblock bluetooth`** then **`power on`**). **Make discoverable** also unblocks rfkill and powers on before advertising.

1. Set the **device name** and tap **Save** (uses `system-alias`; very old BlueZ may require **`Name=`** in **`/etc/bluetooth/main.conf`** instead).
2. Tap **Make discoverable for pairing**, then pair from the phone’s Bluetooth settings.
3. Choose mode **bluetooth** on the **Mode** tab so competing sinks (AirPlay / Spotify) are stopped while using BT audio.
4. If audio routing fails, ensure BlueALSA matches your stack; update **`modes.yaml`** unit names if needed (`systemctl list-units '*bluealsa*'`).

If `apt` reports **`Package 'bluealsa' has no installation candidate`**, install **`bluez-alsa-utils`** instead (the setup script does this automatically).

### Phone shows “Pairing unsuccessful” / cannot complete pairing

Discovery only shows the Pi; **pairing** needs a **Bluetooth agent** on the Pi to accept the phone’s request. Headless systems often had none running.

- **Install/update** this project so **`pi-bluetooth-agent.service`** is present and **active** (uses `expect` + `bluetoothctl` to register a **NoInputNoOutput** agent):

  ```bash
  systemctl status pi-bluetooth-agent
  sudo systemctl enable --now pi-bluetooth-agent
  journalctl -u pi-bluetooth-agent -b --no-pager
  ```

  Fresh **`setup/install.sh`** and **`setup/update.sh`** install **`expect`**, deploy **`/opt/pi-audio-controller/scripts/bt-default-agent.expect`**, and enable the unit.

- **Clear stale pairings**: on the phone, **Forget** “raspberry”; on the Pi run **`bluetoothctl remove XX:XX:XX:XX:XX:XX`** for that device (get MAC from **`bluetoothctl devices`**).

- In the web UI: **Enable Bluetooth** → **Make discoverable** → start pairing on the **phone within the discoverable window** (often a few minutes).

- Optional: in **`/etc/bluetooth/main.conf`** under **`[General]`** add **`JustWorksRepairing = always`**, then **`sudo systemctl restart bluetooth`**, then restart **`pi-bluetooth-agent`**.

## Development

```bash
cd controller
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PI_AUDIO_CONFIG=../config uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

The UI expects `systemctl` and `journalctl` (same as on the Pi).

## Home Assistant integration (piradio-ha)

An independent daemon **`piradio-ha`** bridges D-Bus playback events to MQTT so Home Assistant discovers the Pi as an audio device automatically. It also allows **mode switching** and **auto-mode toggle** directly from Home Assistant.

### Architecture

```
                D-Bus / Files
   ┌──────────────────────────────┐
   │                              │
Spotify ─┐                        │         MQTT
AirPlay ─┼──► piradio-ha ─────────┼──────►──────► Home Assistant
BT A2DP ─┘        ▲               │         (Discovery)
                   │               │
         ◄─── command topics ◄─────┼──── HA select/switch
   └──────────────────────────────┘
              │
              ▼
    Pi Audio Controller API
    (POST /api/mode/*, /api/auto-mode)
```

### What appears in Home Assistant (via MQTT Discovery)

| Entity | Type | Values |
|--------|------|--------|
| `binary_sensor.spotify_playing` | binary_sensor | ON / OFF |
| `binary_sensor.airplay_playing` | binary_sensor | ON / OFF |
| `binary_sensor.bluetooth_playing` | binary_sensor | ON / OFF |
| `binary_sensor.any_audio_playing` | binary_sensor | ON / OFF |
| `sensor.active_audio_source` | sensor | spotify / airplay / bluetooth / idle |
| `select.audio_mode` | select | airplay / spotify / bluetooth |
| `switch.auto_mode` | switch | ON / OFF |

### Detection methods

| Source | D-Bus interface | Property |
|--------|----------------|----------|
| Spotify (raspotify) | File watch (`/var/lib/pi-audio-controller/spotify-playback-state`) | playing/paused/stopped |
| AirPlay (shairport-sync) | `org.gnome.ShairportSync.RemoteControl` | `PlayerState` (0/1/2) |
| Bluetooth A2DP | `org.bluez.MediaTransport1` | `State` (idle/pending/active) |

Falls back to TCP connection check (`ss`) if D-Bus is unavailable for a source.

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
4. Use `select.audio_mode` to switch between airplay/spotify/bluetooth.
5. Use `switch.auto_mode` to toggle automatic source-following mode.

### Logs

```bash
journalctl -u piradio-ha -f
```
