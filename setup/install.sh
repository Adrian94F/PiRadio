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
# Bluetooth ALSA: on Debian 12+ / recent Raspberry Pi OS the APT source package is
# bluez-alsa-utils (provides /usr/bin/bluealsa and usually bluealsa.service).
# The old transitional name "bluealsa" is not always published as an install candidate.
BT_PKG="bluez-alsa-utils"
if ! apt-cache show "${BT_PKG}" &>/dev/null; then
  BT_PKG="bluealsa"
fi
if ! apt-cache show "${BT_PKG}" &>/dev/null; then
  log "Neither bluez-alsa-utils nor bluealsa is in APT; add Debian repos or install BlueALSA from source."
  exit 1
fi

apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  alsa-utils \
  avahi-daemon \
  expect \
  "${BT_PKG}" \
  bluez \
  shairport-sync \
  mosquitto mosquitto-clients \
  curl ca-certificates

# Spotify Connect client (raspotify / librespot wrapper)
if ! dpkg -l raspotify &>/dev/null; then
  log "Installing raspotify…"
  curl -sSL https://dtcooper.github.io/raspotify/install.sh | bash
fi

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

log "Installing Bluetooth pairing agent (required for phone pairing)…"
mkdir -p "${INSTALL_ROOT}/scripts"
install -m 0644 "${REPO_ROOT}/scripts/bt-default-agent.expect" "${INSTALL_ROOT}/scripts/bt-default-agent.expect"
install -m 0755 "${REPO_ROOT}/scripts/spotify-onevent.sh" "${INSTALL_ROOT}/scripts/spotify-onevent.sh"
install -m 0644 "${REPO_ROOT}/systemd/pi-bluetooth-agent.service" /etc/systemd/system/pi-bluetooth-agent.service

# Configure raspotify to call our onevent script for playback detection
RASPOTIFY_CONF="/etc/raspotify/conf"
if [[ -f "${RASPOTIFY_CONF}" ]]; then
  if ! grep -q "^LIBRESPOT_ONEVENT=" "${RASPOTIFY_CONF}"; then
    log "Configuring raspotify --onevent for playback detection…"
    echo "" >> "${RASPOTIFY_CONF}"
    echo "# Pi Audio Controller: report playback state for Home Assistant" >> "${RASPOTIFY_CONF}"
    echo "LIBRESPOT_ONEVENT=\"${INSTALL_ROOT}/scripts/spotify-onevent.sh\"" >> "${RASPOTIFY_CONF}"
  fi
fi

# Allow raspotify (ProtectSystem=strict) to write playback state file
mkdir -p /etc/systemd/system/raspotify.service.d
install -m 0644 "${REPO_ROOT}/systemd/raspotify-override.conf" /etc/systemd/system/raspotify.service.d/pi-audio.conf
systemctl daemon-reload
systemctl restart raspotify.service 2>/dev/null || true

log "Installing PiRadio HA integration (MQTT bridge for Home Assistant)…"
install -m 0755 "${REPO_ROOT}/ha-integration/piradio-ha.py" "${INSTALL_ROOT}/piradio-ha.py"
install -m 0644 "${REPO_ROOT}/ha-integration/requirements.txt" "${INSTALL_ROOT}/requirements-piradio-ha.txt"
if [[ ! -d "${INSTALL_ROOT}/venv-piradio-ha" ]]; then
  python3 -m venv "${INSTALL_ROOT}/venv-piradio-ha"
fi
"${INSTALL_ROOT}/venv-piradio-ha/bin/pip" install --upgrade pip -q
"${INSTALL_ROOT}/venv-piradio-ha/bin/pip" install -r "${INSTALL_ROOT}/requirements-piradio-ha.txt"
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
systemctl enable pi-bluetooth-agent.service
systemctl restart pi-bluetooth-agent.service
systemctl enable pi-audio-controller.service
systemctl restart pi-audio-controller.service
systemctl enable piradio-ha.service
systemctl restart piradio-ha.service

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
log "Done. Open http://${HOST_IP:-<pi-ip>}:8765"
log "Edit ${CONFIG_DIR}/modes.yaml if your systemd units differ (e.g. bluealsa@hci0)."
log "Raspotify: /etc/default/raspotify — Shairport: /etc/shairport-sync.conf"
