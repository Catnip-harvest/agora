#!/usr/bin/env bash
set -euo pipefail

RULE_FILE="/etc/udev/rules.d/99-mira-demo.rules"
temp_file="$(mktemp)"
trap 'rm -f "$temp_file"' EXIT

printf '%s\n' \
  '# Mira demo: LeRobot ACM serial devices' \
  'SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", MODE="0666"' \
  '# Mira demo: DSJ-2062-309 wrist camera' \
  'SUBSYSTEM=="video4linux", ATTRS{serial}=="DSJ-2062-309", MODE="0666"' \
  > "$temp_file"

sudo install -m 0644 "$temp_file" "$RULE_FILE"
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "Installed persistent Mira hardware permissions at $RULE_FILE"
echo "Reconnect the robot and wrist camera once to apply the rules."
