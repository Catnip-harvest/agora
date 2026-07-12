#!/usr/bin/env bash
set -euo pipefail

MIRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOLLOWER_PORT="${ROBOT_PORT:-/dev/ttyACM1}"
LEADER_PORT="${LEADER_PORT:-/dev/ttyACM0}"
WRIST_CAMERA="${SCAN_CAMERA_PATH:-/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0}"

missing=0
for device in "$LEADER_PORT" "$FOLLOWER_PORT"; do
  if [[ ! -e "$device" ]]; then
    echo "MISSING: $device — reconnect the robot USB cable."
    missing=1
  fi
done

if [[ ! -e "$WRIST_CAMERA" ]]; then
  echo "MISSING: $WRIST_CAMERA — reconnect the DSJ-2062-309 wrist camera."
  missing=1
fi

if [[ "$missing" -ne 0 ]]; then
  echo "Mira was not started because demo hardware is incomplete."
  exit 1
fi

camera_device="$(readlink -f "$WRIST_CAMERA")"
echo "Enabling demo access to robot serial ports and wrist camera..."
sudo chmod 666 "$LEADER_PORT" "$FOLLOWER_PORT" "$camera_device"

echo "Follower: $FOLLOWER_PORT"
echo "Leader:   $LEADER_PORT"
echo "Camera:   $WRIST_CAMERA -> $camera_device"
echo "Starting Mira at http://0.0.0.0:8000"
cd "$MIRA_DIR"
exec conda run --no-capture-output -n lerobot uvicorn app:app --host 0.0.0.0 --port 8000
