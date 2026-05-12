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
