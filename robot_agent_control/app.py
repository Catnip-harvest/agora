"""
Mira: Voice-First Physical Workspace Assistant
FastAPI backend for controlling a LeRobot SO-101 robot arm with voice & intent mapping.
"""
import os
import signal
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Base directory setup
BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"

# Ensure static directory exists
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Exact policy path
POLICY_PATH = "outputs/train/smolvla_screwdriver_20k/checkpoints/last/pretrained_model"

# Exact hardcoded robot command requested
ROBOT_COMMAND = [
    "bash", "-c",
    (
        "cd ~/lerobot && conda run -n lerobot lerobot-record "
        "--robot.type=so101_follower "
        "--robot.port=/dev/ttyACM1 "
        "--robot.id=my_follower "
        "--robot.max_relative_target=5 "
        "--robot.disable_torque_on_disconnect=false "
        "--robot.cameras='{"
        'camera1: {type: opencv, index_or_path: "/dev/v4l/by-id/usb-Generic_HD_video_20210901000000-video-index0", width: 640, height: 480, fps: 15, fourcc: "MJPG"}, '
        'camera2: {type: opencv, index_or_path: "/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0", width: 320, height: 240, fps: 15, fourcc: "MJPG"}, '
        'camera3: {type: opencv, index_or_path: "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._USB2.0_HD_UVC_WebCam-video-index0", width: 640, height: 480, fps: 15, fourcc: "MJPG"}'
        "}' "
        "--display_data=false "
        "--dataset.repo_id=local/eval_voice_screwdriver "
        "--dataset.push_to_hub=false "
        "--dataset.num_episodes=1 "
        '--dataset.single_task="pick up the screwdriver and put it on the black workspace" '
        "--dataset.episode_time_s=12 "
        f"--policy.path={POLICY_PATH} "
        "--policy.device=cuda"
    )
]

class RobotRunManager:
    """
    Thread-safe global run manager tracking active subprocess, status, logs, and run history.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.robot_status = "idle"  # idle, running, completed, failed
        self.active_skill: Optional[str] = None
        self.last_run_started_at: Optional[str] = None
        self.last_run_finished_at: Optional[str] = None
        self.latest_logs: deque = deque(maxlen=500)
        self.history: List[dict] = []
        self.last_assistant_message = "Ready. Say 'Mira, I need to fix something' to prep the workspace."
        self.log_counter = 0

    def append_log(self, text: str):
        self.log_counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.latest_logs.append({
            "index": self.log_counter,
            "time": timestamp,
            "text": text.rstrip()
        })

    def start_skill_screwdriver(self) -> dict:
        """
        Launch the trained LeRobot screwdriver policy in a background subprocess.
        Prevents duplicate launches if already active.
        """
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return {
                    "ok": False,
                    "status": "busy",
                    "error": "Robot run is already active. Stop the current run first."
                }

            self.robot_status = "running"
            self.active_skill = "screwdriver"
            self.last_run_started_at = datetime.now(timezone.utc).isoformat()
            self.last_assistant_message = "I’ll prep the repair workspace and bring the screwdriver over."
            self.append_log("🚀 Starting robot skill: Screwdriver Workspace Prep")
            self.append_log(f"📋 Command: {' '.join(ROBOT_COMMAND)}")
            self.append_log("─" * 50)

            try:
                # Use setsid to create a process group for clean cascade shutdown
                proc = subprocess.Popen(
                    ROBOT_COMMAND,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    preexec_fn=os.setsid
                )
                self.process = proc

                # Monitor thread
                monitor = threading.Thread(
                    target=self._monitor_process,
                    args=(proc, self.last_run_started_at),
                    daemon=True
                )
                monitor.start()

                return {
                    "ok": True,
                    "status": "started",
                    "skill": "screwdriver",
                    "message": self.last_assistant_message
                }
            except Exception as e:
                self.robot_status = "failed"
                self.active_skill = None
                self.append_log(f"❌ Subprocess failed to start: {e}")
                return {
                    "ok": False,
                    "status": "failed",
                    "error": str(e)
                }

    def _monitor_process(self, proc: subprocess.Popen, start_time_iso: str):
        """Background thread reading stdout/stderr line by line and recording exit status."""
        try:
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    if line:
                        self.append_log(line)
                    else:
                        break
        except Exception as e:
            self.append_log(f"⚠️ Log read exception: {e}")
        finally:
            proc.wait()
            exit_code = proc.returncode
            finished_time_iso = datetime.now(timezone.utc).isoformat()

            with self.lock:
                self.last_run_finished_at = finished_time_iso
                if self.robot_status == "stopped":
                    self.append_log(f"⛔ Run stopped safely by user (exit code: {exit_code})")
                    self.last_assistant_message = "Robot run stopped."
                elif exit_code == 0:
                    self.robot_status = "completed"
                    self.append_log("✅ Policy run completed successfully.")
                    self.last_assistant_message = "I have brought the screwdriver and placed it on the workspace."
                else:
                    self.robot_status = "failed"
                    self.append_log(f"❌ Policy run exited with non-zero code: {exit_code}")
                    self.last_assistant_message = "Policy run encountered an issue or exited."

                self.history.append({
                    "skill": "screwdriver",
                    "started_at": start_time_iso,
                    "finished_at": finished_time_iso,
                    "status": self.robot_status,
                    "exit_code": exit_code
                })
                self.process = None
                self.active_skill = None

    def stop_active_run(self) -> dict:
        """Terminate the active subprocess safely."""
        with self.lock:
            proc = self.process
            if proc is None or proc.poll() is not None:
                return {
                    "status": "no_active_run",
                    "message": "No active robot policy is currently running."
                }

            self.robot_status = "stopped"
            self.append_log("⛔ Stop signal initiated — terminating subprocess group...")
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGINT)
            except Exception as e:
                self.append_log(f"⚠️ Error sending SIGINT: {e}")

        # Background cleanup thread for graceful kill
        def _terminate_graceful():
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        pass

        threading.Thread(target=_terminate_graceful, daemon=True).start()
        return {"status": "stopped", "message": "Robot process terminated."}

    def get_status_dict(self) -> dict:
        with self.lock:
            return {
                "robot_status": self.robot_status,
                "active_skill": self.active_skill,
                "last_run_started_at": self.last_run_started_at,
                "last_run_finished_at": self.last_run_finished_at,
                "latest_logs": list(self.latest_logs),
                "policy_path": POLICY_PATH,
                "demo_ready": True,
                "last_assistant_message": self.last_assistant_message
            }


# Global run manager
manager = RobotRunManager()

# FastAPI application
app = FastAPI(
    title="Mira — Voice Physical Agent",
    description="Say intent. Mira chooses the robot skill and controls the LeRobot SO-101 arm.",
    version="1.0.0"
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class IntentRequest(BaseModel):
    text: str


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the mobile-friendly web app."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Mira UI not found in static/index.html</h1>", status_code=404)


@app.post("/api/skill/screwdriver")
async def api_skill_screwdriver():
    """
    1. Local robot skill endpoint:
    Starts the trained LeRobot policy in a background subprocess.
    Returns immediately with JSON: {status: "started", skill: "screwdriver"}.
    """
    res = manager.start_skill_screwdriver()
    if not res.get("ok"):
        return JSONResponse(
            {"status": res.get("status", "busy"), "error": res.get("error")},
            status_code=409
        )
    return {
        "status": "started",
        "skill": "screwdriver",
        "message": res["message"]
    }


@app.post("/api/robot/stop")
async def api_robot_stop():
    """
    2. Stop endpoint:
    Attempts to terminate the active subprocess safely.
    """
    return manager.stop_active_run()


@app.get("/api/status")
async def api_status():
    """
    3. Status endpoint:
    Returns robot_status, active_skill, timestamps, logs, policy_path, demo_ready boolean.
    """
    return manager.get_status_dict()


@app.post("/api/voice/intent")
async def api_voice_intent(req: IntentRequest):
    """
    4. Voice intent endpoint:
    Maps natural language to robot skills.
    Triggers screwdriver skill when user says phrases like:
    “I need to fix something”, “bring me the screwdriver”, “prep the workspace”, etc.
    """
    text_lower = req.text.lower().strip()

    # Intent keywords matching screwdriver workspace skill
    screwdriver_keywords = [
        "fix something",
        "fix",
        "repair",
        "screwdriver",
        "prep the workspace",
        "prep repair workspace",
        "prep workspace",
        "get the tool ready",
        "tool ready",
        "bring me the screwdriver",
        "bring screwdriver",
        "help me repair",
        "set up for assembly",
        "assembly"
    ]

    matched = any(kw in text_lower for kw in screwdriver_keywords)

    if matched:
        res = manager.start_skill_screwdriver()
        if not res.get("ok"):
            # Already running
            assistant_msg = "I’m moving the screwdriver to the workspace now."
            return {
                "success": True,
                "intent": req.text,
                "skill": "screwdriver",
                "status": "running",
                "message": assistant_msg
            }

        # Started new run
        assistant_msg = "I’ll prep the repair workspace and bring the screwdriver over."
        return {
            "success": True,
            "intent": req.text,
            "skill": "screwdriver",
            "status": "started",
            "message": assistant_msg
        }

    # Unsupported intent
    fallback_msg = "I can only prepare the screwdriver workspace right now."
    manager.last_assistant_message = fallback_msg
    return {
        "success": False,
        "intent": req.text,
        "skill": None,
        "status": manager.robot_status,
        "message": fallback_msg
    }


@app.post("/api/agora/tool/run_screwdriver_skill")
async def api_agora_tool_run_screwdriver_skill():
    """
    7. Optional Agora integration scaffold:
    This endpoint can be exposed over ngrok as a public URL for Agora ConvoAI tool calling.
    When an Agora Conversational AI voice agent triggers tool call 'run_screwdriver_skill',
    this endpoint launches the physical robot policy and returns JSON compatible with Agora tools.
    """
    res = manager.start_skill_screwdriver()
    return {
        "success": True,
        "message": "I’m moving the screwdriver to the workspace now.",
        "skill": "screwdriver"
    }


# Shortcut routes for UI buttons
@app.post("/api/skill/prep_workspace")
async def api_skill_prep_workspace():
    """Alias for prep repair workspace button -> triggers screwdriver skill."""
    return await api_skill_screwdriver()
