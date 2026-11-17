#!/usr/bin/env bash
# Pi Audio Controller — Raspberry Pi setup (Debian / Raspberry Pi OS).
# Run from repo root: sudo ./setup/install.sh
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/pi-audio-controller}"
CONFIG_DIR="${CONFIG_DIR:-/etc/pi-audio-controller}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '[pi-audio-controller] %s\n' "$*"; }

if [[ "${EUID:-0}" -ne 0 ]]; then
  log "Run as root (sudo)."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  alsa-utils \
  avahi-daemon \
  shairport-sync \
  mosquitto mosquitto-clients \
  curl ca-certificates

select_audio_output() {
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    log "No interactive terminal; keeping the system's current audio output."
    return
  fi

  local -a outputs
  mapfile -t outputs < <(
    aplay -l 2>/dev/null | python3 -c '
import re
import sys

pattern = re.compile(r"^card\s+(\d+):\s*(.+?),\s*device\s+(\d+):\s*(.+)$")
for line in sys.stdin:
    match = pattern.match(line.strip())
    if match:
        card, card_name, device, device_name = match.groups()
        label = f"Card {card}: {card_name.strip()} — {device_name.strip()}"
        print(f"{card},{device}\t{label}")
'
  )

  if (( ${#outputs[@]} == 0 )); then
    log "No ALSA playback devices found; keeping the system's current audio output."
    return
  fi

  printf '\nAvailable audio output devices:\n' > /dev/tty
  local i
  for i in "${!outputs[@]}"; do
    printf '  %d) %s\n' "$((i + 1))" "${outputs[$i]#*$'\t'}" > /dev/tty
  done
  printf '  0) Keep the system default\n' > /dev/tty

  local choice
  while true; do
    printf 'Select audio output [0-%d]: ' "${#outputs[@]}" > /dev/tty
    read -r choice < /dev/tty
    if [[ "$choice" == "0" ]]; then
      log "Keeping the system's current audio output."
      return
    fi
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#outputs[@]} )); then
      break
    fi
    printf 'Invalid selection. Try again.\n' > /dev/tty
  done

  local selected="${outputs[$((choice - 1))]}"
  local audio_id="${selected%%$'\t'*}"
  local audio_label="${selected#*$'\t'}"
  local card="${audio_id%%,*}"
  local device="${audio_id#*,}"

  if [[ -f /etc/asound.conf ]] && ! grep -qF '# Managed by pi-audio-controller' /etc/asound.conf; then
    cp -a /etc/asound.conf "/etc/asound.conf.bak.pi-audio-$(date +%s)"
  fi
  cat > /etc/asound.conf <<EOF
# Managed by pi-audio-controller — default PCM for Pi Audio Controller

pcm.!default {
    type plug
    slave.pcm "hw:${card},${device}"
}
ctl.!default {
    type hw
    card ${card}
}
EOF

  if [[ -f /etc/shairport-sync.conf ]] && ! grep -Eq '^[[:space:]]*output_device[[:space:]]*=[[:space:]]*"default"' /etc/shairport-sync.conf; then
    cp -a /etc/shairport-sync.conf "/etc/shairport-sync.conf.bak.pi-audio-$(date +%s)"
    sed -i -E 's/^([[:space:]]*output_device[[:space:]]*=[[:space:]]*)"[^"]*"/\1"default"/' /etc/shairport-sync.conf
  fi

  mkdir -p /var/lib/pi-audio-controller
  python3 - "$audio_id" "$card" "$device" "$audio_label" <<'PY'
import json
import sys

audio_id, card, device, label = sys.argv[1:]
with open("/var/lib/pi-audio-controller/audio-output.json", "w", encoding="utf-8") as state_file:
    json.dump({"id": audio_id, "card": int(card), "device": int(device), "label": label}, state_file, indent=2)
    state_file.write("\n")
PY
  log "Selected audio output: ${audio_label}."
}

select_audio_output

log "Installing app to ${INSTALL_ROOT}…"
mkdir -p "${INSTALL_ROOT}/controller/static"
for f in "${REPO_ROOT}"/controller/*.py; do
  install -m 0644 "$f" "${INSTALL_ROOT}/controller/$(basename "$f")"
done
install -m 0644 "${REPO_ROOT}/controller/requirements.txt" "${INSTALL_ROOT}/requirements.txt"
cp -a "${REPO_ROOT}/controller/static/." "${INSTALL_ROOT}/controller/static/"

if [[ ! -d "${INSTALL_ROOT}/venv" ]]; then
  python3 -m venv "${INSTALL_ROOT}/venv"
fi
"${INSTALL_ROOT}/venv/bin/pip" install --upgrade pip -q
"${INSTALL_ROOT}/venv/bin/pip" install -r "${INSTALL_ROOT}/requirements.txt"

mkdir -p "${CONFIG_DIR}"
mkdir -p /var/lib/pi-audio-controller
if [[ ! -f "${CONFIG_DIR}/modes.yaml" ]]; then
  install -m 0644 "${REPO_ROOT}/config/modes.yaml" "${CONFIG_DIR}/modes.yaml"
  log "Installed default ${CONFIG_DIR}/modes.yaml (edit unit names for your system)."
else
  log "Keeping existing ${CONFIG_DIR}/modes.yaml."
fi

log "Installing PiRadio HA integration (MQTT bridge for Home Assistant)…"
mkdir -p "${INSTALL_ROOT}/ha_integration"
for f in "${REPO_ROOT}"/ha-integration/*.py; do
  install -m 0644 "$f" "${INSTALL_ROOT}/ha_integration/$(basename "$f")"
done
if [[ ! -d "${INSTALL_ROOT}/venv-piradio-ha" ]]; then
  python3 -m venv "${INSTALL_ROOT}/venv-piradio-ha"
fi
"${INSTALL_ROOT}/venv-piradio-ha/bin/pip" install --upgrade pip -q
"${INSTALL_ROOT}/venv-piradio-ha/bin/pip" install paho-mqtt PyYAML dbus-next asyncinotify
if [[ ! -f "${CONFIG_DIR}/piradio-ha.yaml" ]]; then
  install -m 0644 "${REPO_ROOT}/config/piradio-ha.yaml" "${CONFIG_DIR}/piradio-ha.yaml"
  log "Installed default ${CONFIG_DIR}/piradio-ha.yaml."
fi
install -m 0644 "${REPO_ROOT}/systemd/piradio-ha.service" /etc/systemd/system/piradio-ha.service

log "Enabling Mosquitto MQTT broker…"
mkdir -p /etc/mosquitto/conf.d
if [[ ! -f /etc/mosquitto/conf.d/pi-audio.conf ]]; then
  cat > /etc/mosquitto/conf.d/pi-audio.conf <<'EOF'
# Allow LAN connections for Home Assistant MQTT Discovery.
listener 1883
allow_anonymous true
EOF
  log "Configured Mosquitto for LAN access (/etc/mosquitto/conf.d/pi-audio.conf)."
fi
systemctl enable mosquitto.service
systemctl restart mosquitto.service

log "Installing systemd units…"
install -m 0644 "${REPO_ROOT}/systemd/pi-audio-controller.service" /etc/systemd/system/pi-audio-controller.service
systemctl daemon-reload
systemctl enable shairport-sync.service
systemctl restart shairport-sync.service
systemctl enable pi-audio-controller.service
systemctl restart pi-audio-controller.service
systemctl enable piradio-ha.service
systemctl restart piradio-ha.service

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
log "Done. Open http://${HOST_IP:-<pi-ip>}:8765"
log "Edit ${CONFIG_DIR}/modes.yaml if your systemd units differ."
log "Shairport: /etc/shairport-sync.conf"
