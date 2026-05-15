#!/usr/bin/env bash
# Called by librespot via --onevent. Writes playback state to a file
# that softwarebuttond watches via inotify for instant detection.
# Librespot sets: PLAYER_EVENT, TRACK_ID, POSITION_MS

STATE_FILE="/var/lib/pi-audio-controller/spotify-playback-state"

case "${PLAYER_EVENT}" in
  playing|started|changed)
    echo "playing" > "${STATE_FILE}"
    ;;
  paused)
    echo "paused" > "${STATE_FILE}"
    ;;
  stopped|session_disconnected)
    echo "stopped" > "${STATE_FILE}"
    ;;
esac
