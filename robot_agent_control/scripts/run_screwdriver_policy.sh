#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Run SmolVLA screwdriver-to-workspace policy on LeRobot SO-101
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "🤖 SmolVLA Policy — Screwdriver to Workspace"
echo "════════════════════════════════════════════════════════════"
echo "Start time: $(date)"
echo ""

# Policy checkpoint
POLICY="outputs/train/smolvla_screwdriver_workspace/checkpoints/last/pretrained_model"

# Unique label for this run
LABEL="eval_screwdriver_workspace_$(date +%s)"

echo "📋 Policy:  $POLICY"
echo "📋 Label:   $LABEL"
echo "📋 Task:    pick up the screwdriver and put it on the black workspace"
echo ""
echo "🚀 Starting lerobot-record..."
echo "────────────────────────────────────────────────────────────"

lerobot-record \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.max_relative_target=5 \
  --robot.disable_torque_on_disconnect=false \
  --robot.cameras='{
    camera1: {type: opencv, index_or_path: "/dev/v4l/by-id/usb-Generic_HD_video_20210901000000-video-index0", width: 640, height: 480, fps: 15, fourcc: "MJPG"},
    camera2: {type: opencv, index_or_path: "/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0", width: 320, height: 240, fps: 15, fourcc: "MJPG"},
    camera3: {type: opencv, index_or_path: "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._USB2.0_HD_UVC_WebCam-video-index0", width: 640, height: 480, fps: 15, fourcc: "MJPG"}
  }' \
  --display_data=false \
  --dataset.repo_id="local/$LABEL" \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=1 \
  --dataset.single_task="pick up the screwdriver and put it on the black workspace" \
  --dataset.episode_time_s=15 \
  --policy.path="$POLICY" \
  --policy.device=cuda \
  --policy.empty_cameras=0

EXIT_CODE=$?

echo ""
echo "────────────────────────────────────────────────────────────"
echo "End time: $(date)"
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Policy run completed successfully"
else
    echo "❌ Policy run failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE
