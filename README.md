# Pi Audio Controller

Small web UI and systemd wiring for a Raspberry Pi used as a **local network speaker**: AirPlay 2 (`shairport-sync`), Spotify Connect (`raspotify`), and **Bluetooth A2DP sink** (BlueALSA via **`bluez-alsa-utils`** on Debian 12 / recent Raspberry Pi OS — same daemon as the old `bluealsa` metapackage). Only one “sink mode” should own the speaker at a time; the UI switches modes by starting and stopping the right units.

## Quick install

On the Pi, clone or copy this repo and run:

```bash
sudo ./setup/install.sh
```

Then open `http://<pi-ip>:8765` on your LAN.

Configuration:

- **Modes and unit names:** `/etc/pi-audio-controller/modes.yaml` (installed once; edit to match your distro, e.g. `bluealsa@hci0.service` instead of `bluealsa.service`).
- **Last selected mode (for the UI):** `/var/lib/pi-audio-controller/current-mode`.
- **Raspotify:** `/etc/default/raspotify`.
- **Shairport Sync:** `/etc/shairport-sync.conf`.

## Bluetooth notes

1. Pair your phone from the Pi once (e.g. `bluetoothctl`, `scan on`, `pair`, `trust`, `connect`).
2. In the web UI, choose mode **bluetooth** so competing sinks (AirPlay / Spotify) are stopped.
3. If audio routing fails, ensure BlueALSA is the ALSA/Pulse device your stack expects; unit names vary between Bookworm and older images—update `modes.yaml` accordingly (`systemctl list-units '*bluealsa*'` helps).

If `apt` reports **`Package 'bluealsa' has no installation candidate`**, install **`bluez-alsa-utils`** instead (the setup script does this automatically).

## Development

```bash
cd controller
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PI_AUDIO_CONFIG=../config uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

The UI expects `systemctl` and `journalctl` (same as on the Pi).
