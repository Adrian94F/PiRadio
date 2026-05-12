#!/usr/bin/env bash
# Pi Audio Controller — update deployed files after `git pull` (or let this script pull).
# Run from repo root: sudo ./setup/update.sh
# Options:
#   --pull   Run `git pull` in the repo before updating (requires git + network).
#   --deps   Run pip with upgrade for listed packages (same as fresh install).
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/pi-audio-controller}"
CONFIG_DIR="${CONFIG_DIR:-/etc/pi-audio-controller}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DO_PULL=0
DO_DEPS=0
for arg in "$@"; do
  case "${arg}" in
    --pull) DO_PULL=1 ;;
    --deps) DO_DEPS=1 ;;
    -h|--help)
      printf '%s\n' "Usage: sudo ./setup/update.sh [--pull] [--deps]"
      printf '%s\n' "  --pull   git pull in ${REPO_ROOT} before copying files"
      printf '%s\n' "  --deps   pip install --upgrade pip && pip install -r requirements.txt"
      exit 0
      ;;
    *)
      printf 'Unknown option: %s (try --help)\n' "${arg}" >&2
      exit 2
      ;;
  esac
done

log() { printf '[pi-audio-controller update] %s\n' "$*"; }

if [[ "${EUID:-0}" -ne 0 ]]; then
  log "Run as root (sudo)."
  exit 1
fi

cd "${REPO_ROOT}"

if [[ "${DO_PULL}" -eq 1 ]]; then
  if ! command -v git &>/dev/null; then
    log "git not installed; install git or run git pull yourself, then re-run without --pull."
    exit 1
  fi
  log "Running git pull in ${REPO_ROOT}…"
  git pull --ff-only
fi

log "Deploying app to ${INSTALL_ROOT}…"
mkdir -p "${INSTALL_ROOT}/static"
install -m 0644 "${REPO_ROOT}/controller/app.py" "${INSTALL_ROOT}/app.py"
install -m 0644 "${REPO_ROOT}/controller/requirements.txt" "${INSTALL_ROOT}/requirements.txt"
cp -a "${REPO_ROOT}/controller/static/." "${INSTALL_ROOT}/static/"

if [[ ! -d "${INSTALL_ROOT}/venv" ]]; then
  log "No venv at ${INSTALL_ROOT}/venv — creating…"
  python3 -m venv "${INSTALL_ROOT}/venv"
fi

"${INSTALL_ROOT}/venv/bin/pip" install --upgrade pip -q
if [[ "${DO_DEPS}" -eq 1 ]]; then
  "${INSTALL_ROOT}/venv/bin/pip" install -r "${INSTALL_ROOT}/requirements.txt" --upgrade
else
  "${INSTALL_ROOT}/venv/bin/pip" install -r "${INSTALL_ROOT}/requirements.txt" -q
fi

mkdir -p "${CONFIG_DIR}"
mkdir -p /var/lib/pi-audio-controller
if [[ ! -f "${CONFIG_DIR}/modes.yaml" ]]; then
  install -m 0644 "${REPO_ROOT}/config/modes.yaml" "${CONFIG_DIR}/modes.yaml"
  log "Installed default ${CONFIG_DIR}/modes.yaml (first run)."
else
  log "Leaving existing ${CONFIG_DIR}/modes.yaml unchanged."
fi

log "Refreshing systemd unit…"
install -m 0644 "${REPO_ROOT}/systemd/pi-audio-controller.service" /etc/systemd/system/pi-audio-controller.service
systemctl daemon-reload
systemctl restart pi-audio-controller.service

if systemctl is-active --quiet pi-audio-controller.service; then
  log "Service restarted successfully."
else
  log "Warning: pi-audio-controller.service may not be running — check: journalctl -u pi-audio-controller -b"
  exit 1
fi

log "Update complete."
