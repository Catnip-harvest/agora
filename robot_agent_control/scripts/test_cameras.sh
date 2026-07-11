#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Test all three cameras on the LeRobot SO-101 setup
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "📷 Camera Connectivity Test"
echo "════════════════════════════════════════════════════════════"
echo "Time: $(date)"
echo ""

python3 << 'PYEOF'
import sys

try:
    import cv2
except ImportError:
    print("❌ OpenCV (cv2) is not installed")
    sys.exit(1)

cameras = {
    "camera1 (Overhead)": "/dev/v4l/by-id/usb-Generic_HD_video_20210901000000-video-index0",
    "camera2 (Wrist)":    "/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0",
    "camera3 (Static)":   "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._USB2.0_HD_UVC_WebCam-video-index0",
}

all_ok = True

for name, path in cameras.items():
    print(f"Testing {name}...")
    print(f"  Path: {path}")

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"  ❌ FAILED — could not open camera")
        all_ok = False
        continue

    ret, frame = cap.read()
    cap.release()

    if ret and frame is not None:
        h, w = frame.shape[:2]
        print(f"  ✅ OK — read frame {w}x{h}")
    else:
        print(f"  ❌ FAILED — opened but could not read frame")
        all_ok = False

    print()

if all_ok:
    print("════════════════════════════════════════════════════════════")
    print("✅ All cameras passed")
    sys.exit(0)
else:
    print("════════════════════════════════════════════════════════════")
    print("❌ Some cameras failed — check connections")
    sys.exit(1)
PYEOF
