"""Mira: a gesture-first voice agent for a LeRobot SO-101 follower arm."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from agora_token_v2 import Role_Publisher, RtcTokenBuilder


BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"
SCAN_DIR = STATIC_DIR / "scans"
SCAN_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(BASE_DIR / ".env")
LEROBOT_HOME = Path(os.path.expanduser("~/lerobot"))
POLICY_READY = os.getenv("POLICY_READY", "false").lower() in {"1", "true", "yes", "on"}
VOICE_RESPONSE_DELAY_SECONDS = 8
AGORA_APP_ID = os.getenv("AGORA_APP_ID", "")
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE", "")
AGORA_PIPELINE_ID = os.getenv("AGORA_PIPELINE_ID", "")
AGORA_AGENT_RTC_UID = int(os.getenv("AGORA_AGENT_RTC_UID", "214069"))
AGORA_SESSION_TTL_SECONDS = 3600
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Capture during the four plateaus in the 14.8-second recorded sweep. The
# direction values are user-facing approximations; motion comes from the replay.
SCAN_CAPTURE_SCHEDULE: tuple[tuple[float, int], ...] = ((3.0, -30), (5.5, -10), (8.8, 10), (10.5, 30))
SCAN_REPLAY_DURATION_SECONDS = 14.8
SCAN_CAMERA_PATH = os.getenv(
    "SCAN_CAMERA_PATH",
    "/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0",
)
SCAN_CAMERA_ROTATION = os.getenv("SCAN_CAMERA_ROTATION", "90_ccw")

ROBOT_TYPE = "so101_follower"
ROBOT_PORT = "/dev/ttyACM1"
ROBOT_ID = "my_follower"

GESTURES: dict[str, dict[str, Any]] = {
    "wave": {
        "label": "Wave hello",
        "dataset": "local/motion_hi_wave",
        "episode": 0,
        "started_message": "Mira is waving hello.",
        "completed_message": "Wave complete. Hello from Mira!",
    },
    "dance": {
        "label": "Dance",
        "dataset": "local/motion_dance",
        "episode": 0,
        "started_message": "Mira is dancing.",
        "completed_message": "Dance complete. That was fun!",
    },
    "play_dead": {
        "label": "Play dead",
        "dataset": "local/motion_play_dead",
        "episode": 0,
        "started_message": "Mira is playing dead.",
        "completed_message": "Play-dead routine complete.",
    },
    "nod_yes": {
        "label": "Nod yes",
        "dataset": "local/motion_nod_yes",
        "episode": 0,
        "started_message": "Mira is nodding yes.",
        "completed_message": "Yes! Nod complete.",
    },
    "shake_no": {
        "label": "Shake no",
        "dataset": "local/motion_shake_no",
        "episode": 0,
        "started_message": "Mira is shaking no.",
        "completed_message": "No gesture complete.",
    },
}

SKILL_MAP: dict[str, dict[str, Any]] = {
    "run_scan_motion_skill": {
        "type": "replay_motion",
        "dataset": "local/motion_scan_10s",
        "episode": 0,
        "description": "Slowly scans the scene at four positions, then returns the robot base to center.",
    }
}

BUSY_MESSAGE = "Mira is already moving. Please wait."
AGORA_SYSTEM_PROMPT = """You are Mira, a concise voice-controlled physical robot assistant.

Answer normal conversational questions directly and accurately. Do not call a physical tool for general questions.

For physical requests, call exactly one matching movement tool: wave for greetings, dance for dance or celebration, play dead for play-dead requests, nod yes for a clear affirmative answer, and shake no for a clear negative or unsupported answer. Never retry automatically.

After a movement tool returns started, call wait_for_motion exactly once. Use its returned status to respond. Never claim a movement completed unless the status is completed.

Mira can scan for dynamic visual targets. When the user asks Mira to find, look for, locate, scan for, or check whether she sees a visible object or a visible non-sensitive person description, call scan_for_target with the extracted target phrase. Do not identify people by name or infer sensitive attributes. Report only the visible target and direction."""

SCAN_COMMAND_PATTERN = re.compile(
    r"\b(?:can\s+you\s+)?(?:find|look\s+for|scan\s+for|do\s+you\s+see|where\s+is|locate)\s+(.+?)[?.!]*$",
    re.IGNORECASE,
)
SENSITIVE_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:identify|identity|who\s+is|person'?s?\s+name|named)\b",
        r"\b(?:race|racial|ethnicity|ethnic)\b",
        r"\b(?:religion|religious|muslim|christian|jewish|hindu|buddhist)\b",
        r"\b(?:health|medical|disease|diagnosis|disabled|disability|pregnant)\b",
        r"\b(?:sexuality|sexual\s+orientation|gay|lesbian|transgender)\b",
        r"\b(?:political|party\s+affiliation|citizenship|nationality)\b",
    )
)


def extract_scan_target(text: str) -> str | None:
    """Extract a visible target phrase from a supported scan command."""
    match = SCAN_COMMAND_PATTERN.search(text.strip())
    if not match:
        return None
    target = match.group(1).strip(" \t\r\n?.!")
    target = re.sub(r"^(?:a|an|the)\s+", "", target, flags=re.IGNORECASE)
    return target or None


def validate_scan_target(target: str) -> tuple[bool, str, str]:
    normalized = " ".join(target.strip().split())
    if not normalized or len(normalized) > 160:
        return False, normalized, "Please provide a short visible target description."
    if any(pattern.search(normalized) for pattern in SENSITIVE_TARGET_PATTERNS):
        return (
            False,
            normalized,
            "I can only search for visible non-sensitive descriptions, not identity or sensitive traits.",
        )
    return True, normalized, ""


class VisionTargetResult(BaseModel):
    found: bool = Field(description="True only when the requested target is clearly visible in this image.")
    confidence: Literal["low", "medium", "high"]
    reason: str = Field(description="One short visual reason for the decision without identifying a person.")


def vision_target_check(image_path: str, target: str) -> dict[str, Any]:
    """Check one local JPEG with Gemini 3.1 Flash-Lite and structured output."""
    if not GEMINI_API_KEY:
        return {"found": False, "confidence": "low", "reason": "Gemini API key is not configured."}
    image_bytes = Path(image_path).read_bytes()
    prompt = (
        f'Check whether the visible target "{target}" is clearly present in this image. '
        "Use only visible evidence. Do not identify any person, guess a name, or infer sensitive traits. "
        "Use found=true only for a clear match; otherwise use false."
    )
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            prompt,
        ],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VisionTargetResult,
            temperature=0,
        ),
    )
    parsed = response.parsed
    if not isinstance(parsed, VisionTargetResult):
        parsed = VisionTargetResult.model_validate_json(response.text)
    result = parsed.model_dump()
    # Avoid early stopping on an uncertain visual guess.
    result["found"] = bool(result["found"] and result["confidence"] == "high")
    return result


def capture_scan_image(index: int, angle: int) -> str:
    """Capture and orient one fresh live camera frame."""
    import cv2

    camera = cv2.VideoCapture(SCAN_CAMERA_PATH)
    try:
        if not camera.isOpened():
            raise RuntimeError(f"Could not open scan camera: {SCAN_CAMERA_PATH}")
        ok, frame = camera.read()
        if not ok or frame is None:
            raise RuntimeError("Scan camera opened but did not return an image.")
        if SCAN_CAMERA_ROTATION == "90_ccw":
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif SCAN_CAMERA_ROTATION == "90_cw":
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif SCAN_CAMERA_ROTATION == "180":
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        image_path = SCAN_DIR / f"scan_{index}_{angle}.jpg"
        if not cv2.imwrite(str(image_path), frame):
            raise RuntimeError(f"Could not save scan image: {image_path}")
        return f"/static/scans/{image_path.name}"
    finally:
        camera.release()


def agora_is_configured() -> bool:
    return bool(AGORA_APP_ID and AGORA_APP_CERTIFICATE and AGORA_PIPELINE_ID)


def build_agora_join_payload(channel: str, agent_token: str) -> dict[str, Any]:
    """Build the server-side counterpart to Agora's generated join curl."""
    return {
        "name": f"mira-web-{int(time.time())}",
        "pipeline_id": AGORA_PIPELINE_ID,
        "properties": {
            "asr": {"vendor": "deepgram", "params": {"model": "nova-3", "keyterm": "", "language": "en"}},
            "llm": {
                "vendor": "openai",
                "params": {"model": "gpt-4.1-mini"},
                "system_messages": [{"role": "system", "content": AGORA_SYSTEM_PROMPT}],
                "greeting_message": "Hi, I'm Mira. Ask me to wave, dance, play dead, nod yes, or shake no.",
                "failure_message": "I couldn't start that movement.",
            },
            "tts": {
                "vendor": "minimax",
                "params": {"model": "speech-2.8-turbo", "voice_setting": {"voice_id": "English_radiant_girl"}},
            },
            "parameters": {"silence_config": {"timeout_ms": 10000, "action": "think", "content": "Politely ask if the user is still online."}},
            "idle_timeout": 120,
            "turn_detection": {"interrupt_mode": "interrupt", "prefix_padding_ms": 800, "silence_duration_ms": 480, "threshold": 0.6},
            "advanced_features": {"enable_rtm": True, "enable_sal": False, "enable_aivad": False},
            "channel": channel,
            "agent_rtc_uid": str(AGORA_AGENT_RTC_UID),
            "remote_rtc_uids": ["*"],
            "token": agent_token,
            "enable_string_uid": False,
        },
    }


def post_agora_join(payload: dict[str, Any], agent_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.agora.io/api/conversational-ai-agent/v2/projects/{AGORA_APP_ID}/join",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"agora token={agent_token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def build_gesture_command(skill: str) -> list[str]:
    """Build a camera-free replay command for one recorded gesture."""
    gesture = GESTURES[skill]
    return [
        "conda",
        "run",
        "-n",
        "lerobot",
        "lerobot-replay",
        f"--robot.type={ROBOT_TYPE}",
        f"--robot.port={ROBOT_PORT}",
        f"--robot.id={ROBOT_ID}",
        f"--dataset.repo_id={gesture['dataset']}",
        f"--dataset.episode={gesture['episode']}",
    ]


def build_scan_motion_command() -> list[str]:
    """Build the replay command for the user's recorded four-stop scan."""
    skill = SKILL_MAP["run_scan_motion_skill"]
    return [
        "conda", "run", "-n", "lerobot", "lerobot-replay",
        f"--robot.type={ROBOT_TYPE}",
        f"--robot.port={ROBOT_PORT}",
        f"--robot.id={ROBOT_ID}",
        "--robot.disable_torque_on_disconnect=false",
        f"--dataset.repo_id={skill['dataset']}",
        f"--dataset.episode={skill['episode']}",
    ]


def build_screwdriver_command() -> list[str]:
    """Return the future learned-policy command. It is gated by POLICY_READY."""
    # Future command (intentionally unavailable unless POLICY_READY=true):
    # cd ~/lerobot && conda run -n lerobot lerobot-record \
    #   --robot.type=so101_follower --robot.port=/dev/ttyACM1 \
    #   --robot.id=my_follower --robot.max_relative_target=5 \
    #   --robot.disable_torque_on_disconnect=false \
    #   --robot.cameras='{camera1: {...}, camera2: {...}}' \
    #   --dataset.repo_id=local/eval_voice_screwdriver \
    #   --dataset.single_task="pick up the screwdriver and put it on the black workspace" \
    #   --policy.path=/home/viz/lerobot/outputs/train/act_screwdriver_100_2cam/checkpoints/last/pretrained_model \
    #   --policy.device=cuda
    cameras = (
        '{camera1: {type: opencv, index_or_path: '
        '"/dev/v4l/by-id/usb-Generic_HD_video_20210901000000-video-index0", '
        'width: 640, height: 480, fps: 30, fourcc: "MJPG"}, '
        'camera2: {type: opencv, index_or_path: '
        '"/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0", '
        'width: 320, height: 240, fps: 30, fourcc: "MJPG"}}'
    )
    return [
        "conda", "run", "-n", "lerobot", "lerobot-record",
        f"--robot.type={ROBOT_TYPE}", f"--robot.port={ROBOT_PORT}",
        f"--robot.id={ROBOT_ID}", "--robot.max_relative_target=5",
        "--robot.disable_torque_on_disconnect=false", f"--robot.cameras={cameras}",
        "--display_data=false", "--dataset.repo_id=local/eval_voice_screwdriver",
        "--dataset.push_to_hub=false", "--dataset.num_episodes=1",
        '--dataset.single_task=pick up the screwdriver and put it on the black workspace',
        "--dataset.episode_time_s=10",
        "--policy.path=/home/viz/lerobot/outputs/train/act_screwdriver_100_2cam/checkpoints/last/pretrained_model",
        "--policy.device=cuda",
    ]


class RobotRunManager:
    """Own the single active robot subprocess and its observable runtime state."""

    def __init__(self, log_limit: int = 300, history_limit: int = 50) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.current_run_id: int | None = None
        self.next_run_id = 1
        self.stopped_run_ids: set[int] = set()
        self.robot_status = "idle"
        self.active_skill: str | None = None
        self.last_run_started_at: str | None = None
        self.last_run_finished_at: str | None = None
        self.latest_logs: deque[dict[str, Any]] = deque(maxlen=log_limit)
        self.history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self.last_assistant_message = "Ready. Ask Mira to wave, dance, play dead, nod yes, or shake no."
        self.log_counter = 0
        self.scan_cancel_event = threading.Event()
        self.scan_state: dict[str, Any] = {
            "target": None,
            "angle": None,
            "image_path": None,
            "found": None,
            "confidence": None,
            "message": None,
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def append_log(self, text: str) -> None:
        clean_text = text.rstrip()
        if not clean_text:
            return
        with self.lock:
            self.log_counter += 1
            self.latest_logs.append({
                "index": self.log_counter,
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "text": clean_text,
            })

    def _busy_response(self, requested_skill: str | None = None) -> dict[str, Any]:
        return {
            "success": False,
            "message": BUSY_MESSAGE,
            "skill": requested_skill,
            "status": "busy",
        }

    def start_skill(self, skill: str) -> dict[str, Any]:
        if skill not in GESTURES:
            raise KeyError(skill)
        return self._start_process(
            skill=skill,
            command=build_gesture_command(skill),
            started_message=GESTURES[skill]["started_message"],
            completed_message=GESTURES[skill]["completed_message"],
        )

    def start_screwdriver(self) -> dict[str, Any]:
        if not POLICY_READY:
            return {
                "success": False,
                "message": "The screwdriver skill is coming soon.",
                "skill": "screwdriver",
                "status": "coming_soon",
            }
        return self._start_process(
            skill="screwdriver",
            command=build_screwdriver_command(),
            started_message="Mira is starting the screwdriver policy.",
            completed_message="The screwdriver task is complete.",
        )

    def _start_process(
        self,
        *,
        skill: str,
        command: list[str],
        started_message: str,
        completed_message: str,
    ) -> dict[str, Any]:
        with self.lock:
            if self.process is not None or self.active_skill is not None:
                return self._busy_response(skill)

            run_id = self.next_run_id
            self.next_run_id += 1
            started_at = self._now()
            self.robot_status = "running"
            self.active_skill = skill
            self.last_run_started_at = started_at
            self.last_run_finished_at = None
            self.last_assistant_message = started_message
            self.append_log(f"Starting skill: {skill}")
            self.append_log(f"Command: {' '.join(command)}")

            try:
                process = subprocess.Popen(
                    command,
                    cwd=LEROBOT_HOME,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except Exception as exc:
                finished_at = self._now()
                self.robot_status = "failed"
                self.active_skill = None
                self.last_run_finished_at = finished_at
                self.last_assistant_message = f"Mira could not start {skill}."
                self.append_log(f"Failed to start {skill}: {exc}")
                self.history.appendleft({
                    "run_id": run_id,
                    "skill": skill,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "status": "failed",
                    "exit_code": None,
                })
                return {
                    "success": False,
                    "message": self.last_assistant_message,
                    "skill": skill,
                    "status": "failed",
                }

            self.process = process
            self.current_run_id = run_id
            threading.Thread(
                target=self._monitor_process,
                args=(process, run_id, skill, started_at, completed_message),
                daemon=True,
                name=f"mira-monitor-{run_id}",
            ).start()
            return {
                "success": True,
                "message": started_message,
                "skill": skill,
                "status": "started",
            }

    def _monitor_process(
        self,
        process: subprocess.Popen[str],
        run_id: int,
        skill: str,
        started_at: str,
        completed_message: str,
    ) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self.append_log(line)
            exit_code = process.wait()
        except Exception as exc:
            self.append_log(f"Log monitor error for {skill}: {exc}")
            exit_code = process.poll()
            if exit_code is None:
                exit_code = -1

        finished_at = self._now()
        with self.lock:
            was_stopped = run_id in self.stopped_run_ids
            self.stopped_run_ids.discard(run_id)
            final_status = "stopped" if was_stopped else ("completed" if exit_code == 0 else "failed")
            self.append_log(f"Skill {skill} {final_status} (exit code: {exit_code}).")
            self.history.appendleft({
                "run_id": run_id,
                "skill": skill,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": final_status,
                "exit_code": exit_code,
            })

            # A stale monitor may record its history, but never overwrite a newer run.
            if self.current_run_id != run_id or self.process is not process:
                return
            self.robot_status = final_status
            self.active_skill = None
            self.last_run_finished_at = finished_at
            if final_status == "completed":
                self.last_assistant_message = completed_message
            elif final_status == "stopped":
                self.last_assistant_message = "Mira stopped moving."
            else:
                self.last_assistant_message = f"The {skill} skill failed. Check the logs."
            self.process = None
            self.current_run_id = None

    def stop_active_run(self) -> dict[str, Any]:
        with self.lock:
            process = self.process
            run_id = self.current_run_id
            skill = self.active_skill
            if skill == "scan":
                self.scan_cancel_event.set()
                self.robot_status = "stopped"
                self.last_assistant_message = "Stopping Mira's scan now."
                self.append_log("Stop requested for visual scan.")
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    except Exception as exc:
                        self.append_log(f"Could not stop scan replay: {exc}")
                return {
                    "success": True,
                    "message": "Stopping Mira's scan now.",
                    "skill": "scan",
                    "status": "stopped",
                }
            if process is None or run_id is None:
                return {
                    "success": False,
                    "message": "Mira is not moving right now.",
                    "skill": None,
                    "status": self.robot_status,
                }
            self.stopped_run_ids.add(run_id)
            self.robot_status = "stopped"
            self.last_assistant_message = "Stopping Mira now."
            self.append_log(f"Stop requested for {skill}; sending SIGINT.")
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                self.append_log("Process already exited while STOP was requested.")
            except Exception as exc:
                self.append_log(f"Could not send SIGINT: {exc}")

        threading.Thread(
            target=self._escalate_stop,
            args=(process, run_id),
            daemon=True,
            name=f"mira-stop-{run_id}",
        ).start()
        return {
            "success": True,
            "message": "Stopping Mira now.",
            "skill": skill,
            "status": "stopped",
        }

    def _escalate_stop(self, process: subprocess.Popen[str], run_id: int) -> None:
        try:
            process.wait(timeout=4)
            return
        except subprocess.TimeoutExpired:
            self.append_log("SIGINT timeout; sending SIGTERM.")
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            self.append_log("SIGTERM timeout; sending SIGKILL.")
        except ProcessLookupError:
            return
        except Exception as exc:
            self.append_log(f"Could not send SIGTERM: {exc}")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:
            self.append_log(f"Could not send SIGKILL: {exc}")

    def get_status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "success": True,
                "message": self.last_assistant_message,
                "skill": self.active_skill,
                "status": self.robot_status,
                "robot_status": self.robot_status,
                "active_skill": self.active_skill,
                "last_run_started_at": self.last_run_started_at,
                "last_run_finished_at": self.last_run_finished_at,
                "latest_logs": list(self.latest_logs),
                "history": list(self.history),
                "screwdriver_available": POLICY_READY,
                "scan": dict(self.scan_state),
            }

    def begin_scan(self, target: str) -> dict[str, Any] | None:
        with self.lock:
            if self.process is not None or self.active_skill is not None:
                return self._busy_response("scan")
            self.robot_status = "running"
            self.active_skill = "scan"
            self.current_run_id = self.next_run_id
            self.next_run_id += 1
            self.last_run_started_at = self._now()
            self.last_run_finished_at = None
            self.last_assistant_message = f"Scanning for {target}."
            self.scan_cancel_event.clear()
            self.scan_state = {
                "target": target,
                "angle": None,
                "image_path": None,
                "found": None,
                "confidence": None,
                "message": self.last_assistant_message,
            }
            self.append_log(f"Starting visual scan for: {target}")
            return None

    def update_scan(self, **changes: Any) -> None:
        with self.lock:
            self.scan_state.update(changes)

    def finish_scan(self, result: dict[str, Any], *, started_at: str) -> None:
        with self.lock:
            stopped = self.scan_cancel_event.is_set()
            status = "stopped" if stopped else ("failed" if not result.get("success") else "completed")
            finished_at = self._now()
            self.robot_status = status
            self.active_skill = None
            self.last_run_finished_at = finished_at
            self.last_assistant_message = result["message"]
            self.scan_state.update({
                "found": result.get("found"),
                "confidence": result.get("confidence"),
                "angle": result.get("angle", self.scan_state.get("angle")),
                "image_path": result.get("image_path", self.scan_state.get("image_path")),
                "message": result["message"],
            })
            self.history.appendleft({
                "run_id": self.current_run_id,
                "skill": "scan",
                "target": result.get("target"),
                "started_at": started_at,
                "finished_at": finished_at,
                "status": status,
                "exit_code": result.get("exit_code"),
            })
            self.process = None
            self.current_run_id = None
            self.append_log(f"Visual scan {status}: {result['message']}")
            self.scan_cancel_event.clear()


def normalize_intent(text: str) -> str:
    normalized = text.lower().replace("’", "'")
    normalized = re.sub(r"[^a-z0-9']+", " ", normalized)
    return " ".join(normalized.split())


INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("play_dead", ("play dead", "act dead", "sleep")),
    ("dance", ("show me a move", "celebrate", "dance")),
    ("nod_yes", ("can you do that", "is that possible", "correct", "yes")),
    ("shake_no", ("can't do that", "cant do that", "not possible", "unsupported", "no")),
    ("wave", ("say hi", "greet me", "hello", "wave")),
)


def match_intent(text: str) -> str | None:
    normalized = f" {normalize_intent(text)} "
    for skill, phrases in INTENT_RULES:
        for phrase in phrases:
            if f" {phrase} " in normalized:
                return skill
    return None


manager = RobotRunManager()


def _scan_direction(angle: int) -> str:
    if angle < 0:
        return f"about {abs(angle)} degrees to my left"
    if angle > 0:
        return f"about {angle} degrees to my right"
    return "in front of me"


def _spoken_target(target: str) -> str:
    if target.lower().startswith(("a ", "an ", "the ", "someone", "something", "people")):
        return target
    article = "an" if target[:1].lower() in "aeiou" else "a"
    return f"{article} {target}"


def _launch_scan_replay() -> subprocess.Popen[str]:
    command = build_scan_motion_command()
    manager.append_log(f"Command: {' '.join(command)}")
    return subprocess.Popen(
        command,
        cwd=LEROBOT_HOME,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )


def _collect_scan_logs(process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for line in process.stdout:
            manager.append_log(line)


def _stop_scan_replay(process: subprocess.Popen[str]) -> int | None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    try:
        return process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            return process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            return process.wait(timeout=2)


def _wait_until_capture(started: float, capture_at: float) -> bool:
    remaining = capture_at - (time.monotonic() - started)
    return remaining > 0 and manager.scan_cancel_event.wait(remaining)


def scan_for_target(target: str) -> dict[str, Any]:
    """Run one privacy-checked visual scan and return the best target result."""
    allowed, normalized_target, refusal = validate_scan_target(target)
    if not allowed:
        return {
            "success": False,
            "found": False,
            "target": normalized_target,
            "status": "refused",
            "message": refusal,
        }

    busy = manager.begin_scan(normalized_target)
    if busy is not None:
        return {
            **busy,
            "found": False,
            "target": normalized_target,
        }

    started_at = manager.last_run_started_at or RobotRunManager._now()
    result: dict[str, Any]
    process: subprocess.Popen[str] | None = None
    try:
        process = _launch_scan_replay()
        with manager.lock:
            manager.process = process
        threading.Thread(target=_collect_scan_logs, args=(process,), daemon=True).start()
        replay_started = time.monotonic()
        for index, (capture_at, angle) in enumerate(SCAN_CAPTURE_SCHEDULE, start=1):
            if _wait_until_capture(replay_started, capture_at) or manager.scan_cancel_event.is_set():
                result = {
                    "success": True,
                    "found": False,
                    "target": normalized_target,
                    "status": "stopped",
                    "message": "I stopped the visual scan.",
                }
                break
            if process.poll() is not None:
                raise RuntimeError(f"scan replay exited early with code {process.poll()}")
            manager.update_scan(angle=angle, message=f"Capturing scan position {index} of 4.")
            image_url = capture_scan_image(index, angle)
            manager.update_scan(image_path=image_url)
            image_file = STATIC_DIR / image_url.removeprefix("/static/")
            check = vision_target_check(str(image_file), normalized_target)
            manager.append_log(
                f"Vision position {index}: confidence={check.get('confidence', 'low')}; "
                f"{check.get('reason', 'No reason returned.')}"
            )
            if check.get("found"):
                exit_code = _stop_scan_replay(process)
                result = {
                    "success": True, "found": True, "target": normalized_target,
                    "angle": angle, "confidence": str(check.get("confidence", "high")),
                    "image_path": image_url, "exit_code": exit_code,
                    "message": f"I found {_spoken_target(normalized_target)} {_scan_direction(angle)}.",
                }
                break
        else:
            if _wait_until_capture(replay_started, SCAN_REPLAY_DURATION_SECONDS) or manager.scan_cancel_event.is_set():
                result = {
                    "success": True, "found": False, "target": normalized_target,
                    "status": "stopped", "message": "I stopped the visual scan.",
                }
                _stop_scan_replay(process)
                manager.finish_scan(result, started_at=started_at)
                return result
            # The recording has now completed its return-to-center segment. Stop
            # the conda wrapper explicitly; it can otherwise remain alive while
            # the follower continues holding its final pose.
            exit_code = _stop_scan_replay(process)
            result = {
                "success": True, "found": False, "target": normalized_target,
                "confidence": "low", "image_path": manager.scan_state.get("image_path"),
                "exit_code": exit_code,
                "message": f"I scanned the area but did not confidently find {_spoken_target(normalized_target)}.",
            }
    except Exception as exc:
        if process is not None:
            _stop_scan_replay(process)
        result = {
            "success": False,
            "found": False,
            "target": normalized_target,
            "status": "failed",
            "message": f"I could not complete the visual scan: {exc}",
        }
    manager.finish_scan(result, started_at=started_at)
    return result


app = FastAPI(
    title="Mira — Voice Physical Agent",
    description="Intent in, motion out. Gesture replay for a LeRobot SO-101.",
    version="2.0.0",
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class IntentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ScanRequest(BaseModel):
    target: str = Field(min_length=1, max_length=160)


class AgoraSessionResponse(BaseModel):
    success: bool
    message: str
    app_id: str | None = None
    channel: str | None = None
    uid: int | None = None
    token: str | None = None
    agent_id: str | None = None


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    state = manager.get_status()
    state["agora_web_voice_available"] = agora_is_configured()
    return state


@app.post("/api/agora/session/start", response_model=AgoraSessionResponse)
async def api_agora_start_session() -> AgoraSessionResponse:
    """Create an Agora agent session and issue a browser-only RTC token."""
    if not agora_is_configured():
        return AgoraSessionResponse(success=False, message="Agora web voice is not configured on this server.")

    channel = f"mira-web-{uuid.uuid4().hex[:12]}"
    client_uid = int.from_bytes(os.urandom(4), "big") % 1_000_000_000 + 1
    agent_token = RtcTokenBuilder.build_token_with_uid(
        AGORA_APP_ID, AGORA_APP_CERTIFICATE, channel, AGORA_AGENT_RTC_UID, Role_Publisher, AGORA_SESSION_TTL_SECONDS
    )
    client_token = RtcTokenBuilder.build_token_with_uid(
        AGORA_APP_ID, AGORA_APP_CERTIFICATE, channel, client_uid, Role_Publisher, AGORA_SESSION_TTL_SECONDS
    )
    try:
        result = await asyncio.to_thread(post_agora_join, build_agora_join_payload(channel, agent_token), agent_token)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return AgoraSessionResponse(success=False, message=f"Agora rejected the session request: {detail[:220]}")
    except Exception as exc:
        return AgoraSessionResponse(success=False, message=f"Could not start Agora voice: {exc}")

    return AgoraSessionResponse(
        success=True,
        message="Agora voice session started.",
        app_id=AGORA_APP_ID,
        channel=channel,
        uid=client_uid,
        token=client_token,
        agent_id=str(result.get("agent_id", "")) or None,
    )


def direct_skill_response(skill: str) -> dict[str, Any] | JSONResponse:
    result = manager.start_skill(skill)
    status_code = 409 if result["status"] == "busy" else (500 if result["status"] == "failed" else 200)
    return result if status_code == 200 else JSONResponse(result, status_code=status_code)


@app.post("/api/skill/wave")
async def api_skill_wave():
    return direct_skill_response("wave")


@app.post("/api/skill/dance")
async def api_skill_dance():
    return direct_skill_response("dance")


@app.post("/api/skill/play_dead")
async def api_skill_play_dead():
    return direct_skill_response("play_dead")


@app.post("/api/skill/nod_yes")
async def api_skill_nod_yes():
    return direct_skill_response("nod_yes")


@app.post("/api/skill/shake_no")
async def api_skill_shake_no():
    return direct_skill_response("shake_no")


@app.post("/api/skill/scan_motion")
async def api_skill_scan_motion():
    result = manager._start_process(
        skill="scan_motion",
        command=build_scan_motion_command(),
        started_message="Mira is running the recorded scan motion.",
        completed_message="Mira completed the recorded scan motion.",
    )
    status_code = 409 if result["status"] == "busy" else (500 if result["status"] == "failed" else 200)
    return result if status_code == 200 else JSONResponse(result, status_code=status_code)


@app.post("/api/skill/screwdriver")
async def api_skill_screwdriver():
    result = manager.start_screwdriver()
    status_code = 409 if result["status"] == "busy" else (500 if result["status"] == "failed" else 200)
    return result if status_code == 200 else JSONResponse(result, status_code=status_code)


@app.post("/api/robot/stop")
async def api_robot_stop() -> dict[str, Any]:
    return manager.stop_active_run()


@app.post("/api/scan")
async def api_scan(request: ScanRequest):
    result = await asyncio.to_thread(scan_for_target, request.target)
    status_code = 409 if result.get("status") == "busy" else (400 if result.get("status") == "refused" else 200)
    return result if status_code == 200 else JSONResponse(result, status_code=status_code)


@app.post("/api/voice/intent")
async def api_voice_intent(request: IntentRequest) -> dict[str, Any]:
    scan_target = extract_scan_target(request.text)
    if scan_target is not None:
        return await asyncio.to_thread(scan_for_target, scan_target)

    skill = match_intent(request.text)
    if skill is not None:
        return manager.start_skill(skill)

    if manager.get_status()["active_skill"] is not None:
        return {
            "success": False,
            "message": f"I don't know that move yet. {BUSY_MESSAGE}",
            "skill": None,
            "status": "busy",
        }

    result = manager.start_skill("shake_no")
    result["success"] = False
    result["message"] = "I don't know that move yet, so I'll shake no."
    return result


def agora_skill_response(skill: str) -> dict[str, Any]:
    """Agora tool calls always return HTTP 200, including busy responses."""
    return manager.start_skill(skill)


@app.post("/api/agora/tool/run_wave_skill")
async def agora_wave() -> dict[str, Any]:
    return agora_skill_response("wave")


@app.post("/api/agora/tool/run_dance_skill")
async def agora_dance() -> dict[str, Any]:
    return agora_skill_response("dance")


@app.post("/api/agora/tool/run_play_dead_skill")
async def agora_play_dead() -> dict[str, Any]:
    return agora_skill_response("play_dead")


@app.post("/api/agora/tool/run_nod_yes_skill")
async def agora_nod_yes() -> dict[str, Any]:
    return agora_skill_response("nod_yes")


@app.post("/api/agora/tool/run_shake_no_skill")
async def agora_shake_no() -> dict[str, Any]:
    return agora_skill_response("shake_no")


@app.post("/api/agora/tool/scan_for_target")
async def agora_scan_for_target(request: ScanRequest) -> dict[str, Any]:
    """Agora tool wrapper around the same privacy-checked scan implementation."""
    return await asyncio.to_thread(scan_for_target, request.target)


@app.post("/api/agora/tool/wait_for_motion")
async def agora_wait_for_motion() -> dict[str, Any]:
    """Wait before an Agora agent speaks, without blocking robot execution."""
    await asyncio.sleep(VOICE_RESPONSE_DELAY_SECONDS)
    state = manager.get_status()
    current_status = state["status"]
    if current_status == "completed":
        message = "Mira finished the motion."
    elif current_status == "running":
        message = "Mira is still moving."
    elif current_status == "failed":
        message = "Mira could not complete the motion."
    elif current_status == "stopped":
        message = "Mira stopped moving."
    else:
        message = "Mira is ready."
    return {
        "success": current_status != "failed",
        "message": message,
        "skill": state["active_skill"],
        "status": current_status,
    }
