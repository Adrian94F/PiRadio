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
  avahi-daemon \
  "${BT_PKG}" \
  bluez \
  shairport-sync \
  curl ca-certificates

# Spotify Connect client (raspotify / librespot wrapper)
if ! dpkg -l raspotify &>/dev/null; then
  log "Installing raspotify…"
  curl -sSL https://dtcooper.github.io/raspotify/install.sh | bash
fi

log "Installing app to ${INSTALL_ROOT}…"
mkdir -p "${INSTALL_ROOT}/static"
install -m 0644 "${REPO_ROOT}/controller/app.py" "${INSTALL_ROOT}/app.py"
install -m 0644 "${REPO_ROOT}/controller/requirements.txt" "${INSTALL_ROOT}/requirements.txt"
cp -a "${REPO_ROOT}/controller/static/." "${INSTALL_ROOT}/static/"

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

log "Installing systemd unit…"
install -m 0644 "${REPO_ROOT}/systemd/pi-audio-controller.service" /etc/systemd/system/pi-audio-controller.service
systemctl daemon-reload
systemctl enable pi-audio-controller.service
systemctl restart pi-audio-controller.service

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
log "Done. Open http://${HOST_IP:-<pi-ip>}:8765"
log "Edit ${CONFIG_DIR}/modes.yaml if your systemd units differ (e.g. bluealsa@hci0)."
log "Raspotify: /etc/default/raspotify — Shairport: /etc/shairport-sync.conf"
