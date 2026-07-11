"""Central configuration for Physical AI Workspace Agent."""
import os
from pathlib import Path

# Project paths
BASE_DIR = Path(__file__).parent.resolve()
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Robot hardware
ROBOT_PORT = "/dev/ttyACM1"
LEADER_PORT = "/dev/ttyACM0"
ROBOT_TYPE = "so101_follower"
ROBOT_ID = "my_follower"
MAX_RELATIVE_TARGET = 5

# Camera paths (by USB ID for stability)
CAMERAS = {
    "camera1": {
        "type": "opencv",
        "path": "/dev/v4l/by-id/usb-Generic_HD_video_20210901000000-video-index0",
        "width": 640, "height": 480, "fps": 15, "fourcc": "MJPG",
        "label": "Overhead Camera"
    },
    "camera2": {
        "type": "opencv",
        "path": "/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0",
        "width": 320, "height": 240, "fps": 15, "fourcc": "MJPG",
        "label": "Wrist Camera"
    },
    "camera3": {
        "type": "opencv",
        "path": "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._USB2.0_HD_UVC_WebCam-video-index0",
        "width": 640, "height": 480, "fps": 15, "fourcc": "MJPG",
        "label": "Static Camera"
    },
}

# Policy / Model
POLICY_PATH = "outputs/train/smolvla_screwdriver_workspace/checkpoints/last/pretrained_model"
POLICY_DEVICE = "cuda"

# Dataset
DATASET_TASK = "pick up the screwdriver and put it on the black workspace"
DATASET_NAME = "bring_screwdriver_workspace_30"
EPISODE_TIME_S = 15
NUM_EPISODES = 1

# Conda
CONDA_ENV = "lerobot"

# Server settings
MAX_LOG_LINES = 1000
STOP_GRACE_PERIOD = 5  # seconds between SIGINT and SIGKILL
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000
