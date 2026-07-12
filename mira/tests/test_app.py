from __future__ import annotations

import io
import asyncio
import json
import subprocess
import threading
import time
from unittest.mock import AsyncMock, patch

import app as mira


class FakeProcess:
    _next_pid = 4100

    def __init__(self, lines: list[str] | None = None, exit_code: int = 0, block: bool = False):
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self.stdout = io.StringIO("".join(lines or []))
        self.returncode = None
        self._exit_code = exit_code
        self._done = threading.Event()
        if not block:
            self._done.set()

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        self.returncode = self._exit_code
        return self.returncode

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def finish(self, exit_code: int | None = None):
        if exit_code is not None:
            self._exit_code = exit_code
        self._done.set()


def wait_until(predicate, timeout: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met")


def fresh_manager(log_limit: int = 300):
    manager = mira.RobotRunManager(log_limit=log_limit)
    mira.manager = manager
    return manager


def test_every_gesture_builds_exact_camera_free_replay_command():
    datasets = {
        "wave": "local/motion_hi_wave",
        "dance": "local/motion_dance",
        "play_dead": "local/motion_play_dead",
        "nod_yes": "local/motion_nod_yes",
        "shake_no": "local/motion_shake_no",
    }
    for skill, dataset in datasets.items():
        command = mira.build_gesture_command(skill)
        assert command[:5] == ["conda", "run", "-n", "lerobot", "lerobot-replay"]
        assert f"--robot.port={mira.ROBOT_PORT}" in command
        assert f"--robot.id={mira.ROBOT_ID}" in command
        assert f"--dataset.repo_id={dataset}" in command
        assert "--dataset.episode=0" in command
        assert not any("camera" in argument.lower() or "train" in argument.lower() for argument in command)


def test_scan_motion_builds_exact_recorded_replay_command():
    assert mira.build_scan_motion_command() == [
        "conda", "run", "-n", "lerobot", "lerobot-replay",
        "--robot.type=so101_follower", "--robot.port=/dev/ttyACM1",
        "--robot.id=my_follower", "--robot.disable_torque_on_disconnect=false",
        "--dataset.repo_id=local/motion_scan_10s", "--dataset.episode=0",
    ]


def test_start_returns_immediately_rejects_duplicate_and_records_success():
    manager = fresh_manager()
    process = FakeProcess(["robot ready\n", "moving\n"], block=True)
    with patch.object(mira.subprocess, "Popen", return_value=process) as popen:
        started = manager.start_skill("wave")
        busy = manager.start_skill("dance")
        assert started["status"] == "started"
        assert busy == {
            "success": False,
            "message": mira.BUSY_MESSAGE,
            "skill": "dance",
            "status": "busy",
        }
        assert popen.call_args.kwargs["cwd"] == mira.LEROBOT_HOME
        assert popen.call_args.kwargs["start_new_session"] is True
        process.finish(0)
        wait_until(lambda: manager.get_status()["status"] == "completed")
    status = manager.get_status()
    assert status["history"][0]["skill"] == "wave"
    assert status["history"][0]["exit_code"] == 0
    assert any(log["text"] == "robot ready" for log in status["latest_logs"])


def test_failed_exit_and_startup_failure_enter_history():
    manager = fresh_manager()
    failed_process = FakeProcess(exit_code=7)
    with patch.object(mira.subprocess, "Popen", return_value=failed_process):
        manager.start_skill("dance")
        wait_until(lambda: manager.get_status()["status"] == "failed")
    assert manager.get_status()["history"][0]["exit_code"] == 7

    with patch.object(mira.subprocess, "Popen", side_effect=FileNotFoundError("conda missing")):
        result = manager.start_skill("wave")
    assert result["status"] == "failed"
    assert manager.get_status()["history"][0]["exit_code"] is None


def test_log_buffer_is_bounded():
    manager = fresh_manager(log_limit=3)
    for index in range(8):
        manager.append_log(f"line {index}")
    assert [line["text"] for line in manager.get_status()["latest_logs"]] == ["line 5", "line 6", "line 7"]


def test_stop_is_non_blocking_and_preserves_stopped_status():
    manager = fresh_manager()
    process = FakeProcess(block=True)
    with patch.object(mira.subprocess, "Popen", return_value=process), patch.object(mira.os, "killpg") as killpg:
        manager.start_skill("play_dead")
        result = manager.stop_active_run()
        assert result["status"] == "stopped"
        killpg.assert_called_once_with(process.pid, mira.signal.SIGINT)
        process.finish(-2)
        wait_until(lambda: manager.get_status()["active_skill"] is None)
    assert manager.get_status()["status"] == "stopped"
    assert manager.get_status()["history"][0]["status"] == "stopped"


def test_stop_escalates_to_sigterm_and_sigkill():
    manager = fresh_manager()
    process = FakeProcess(block=True)
    manager.process = process
    manager.current_run_id = 9
    manager.active_skill = "wave"
    with patch.object(process, "wait", side_effect=[subprocess.TimeoutExpired("fake", 4), subprocess.TimeoutExpired("fake", 2)]), patch.object(mira.os, "killpg") as killpg:
        manager._escalate_stop(process, 9)
    assert killpg.call_args_list[0].args == (process.pid, mira.signal.SIGTERM)
    assert killpg.call_args_list[1].args == (process.pid, mira.signal.SIGKILL)


def test_voice_phrase_mapping():
    expected = {
        "say hi": "wave", "wave": "wave", "hello": "wave", "greet me": "wave",
        "dance": "dance", "celebrate": "dance", "show me a move": "dance",
        "play dead": "play_dead", "act dead": "play_dead", "sleep": "play_dead",
        "yes": "nod_yes", "correct": "nod_yes", "can you do that": "nod_yes", "is that possible": "nod_yes",
        "no": "shake_no", "not possible": "shake_no", "unsupported": "shake_no", "can't do that": "shake_no",
    }
    for phrase, skill in expected.items():
        assert mira.match_intent(f"Mira, {phrase}!") == skill


def test_api_direct_busy_agora_busy_and_status_shape():
    manager = fresh_manager()
    process = FakeProcess(block=True)
    with patch.object(mira.subprocess, "Popen", return_value=process):
        started = asyncio.run(mira.api_skill_wave())
        assert started["status"] == "started"
        direct = asyncio.run(mira.api_skill_dance())
        assert direct.status_code == 409
        assert json.loads(direct.body)["status"] == "busy"
        agora = asyncio.run(mira.agora_dance())
        assert agora["success"] is False
        status = asyncio.run(mira.api_status())
        assert {"status", "active_skill", "latest_logs", "history", "screwdriver_available"} <= status.keys()
        process.finish()


def test_unsupported_intent_shakes_no_only_when_idle():
    manager = fresh_manager()
    first = FakeProcess(block=True)
    with patch.object(mira.subprocess, "Popen", return_value=first):
        response = asyncio.run(mira.api_voice_intent(mira.IntentRequest(text="make coffee")))
        assert response["success"] is False
        assert response["skill"] == "shake_no"
        assert response["status"] == "started"
        busy = asyncio.run(mira.api_voice_intent(mira.IntentRequest(text="make tea")))
        assert busy["status"] == "busy"
        assert busy["skill"] is None
        first.finish()


def test_screwdriver_is_disabled_by_default():
    fresh_manager()
    with patch.object(mira, "POLICY_READY", False), patch.object(mira.subprocess, "Popen") as popen:
        response = asyncio.run(mira.api_skill_screwdriver())
    assert response["status"] == "coming_soon"
    popen.assert_not_called()


def test_all_required_routes_are_registered():
    paths = {route.path for route in mira.app.routes}
    assert {
        "/", "/api/status", "/api/skill/wave", "/api/skill/dance",
        "/api/skill/play_dead", "/api/skill/nod_yes", "/api/skill/shake_no",
        "/api/skill/scan_motion",
        "/api/skill/screwdriver", "/api/robot/stop", "/api/voice/intent",
        "/api/agora/tool/run_wave_skill", "/api/agora/tool/run_dance_skill",
        "/api/agora/tool/run_play_dead_skill", "/api/agora/tool/run_nod_yes_skill",
        "/api/agora/tool/run_shake_no_skill", "/api/agora/tool/wait_for_motion",
        "/api/scan", "/api/agora/tool/scan_for_target",
    } <= paths


def test_agora_wait_for_motion_delays_then_reports_current_state():
    manager = fresh_manager()
    with patch.object(mira.asyncio, "sleep", new=AsyncMock()) as sleep:
        response = asyncio.run(mira.agora_wait_for_motion())
    sleep.assert_awaited_once_with(8)
    assert response["status"] == "idle"
    assert response["message"] == "Mira is ready."


def test_agora_join_payload_uses_a_server_generated_agent_token():
    payload = mira.build_agora_join_payload("mira-web-test", "agent-token")
    assert payload["pipeline_id"] == mira.AGORA_PIPELINE_ID
    assert payload["properties"]["channel"] == "mira-web-test"
    assert payload["properties"]["token"] == "agent-token"
    assert payload["properties"]["agent_rtc_uid"] == str(mira.AGORA_AGENT_RTC_UID)
    assert "raw Agora token" not in payload["properties"]["llm"]["system_messages"][0]["content"]


def test_agora_tokens_use_current_v007_format():
    token = mira.RtcTokenBuilder.build_token_with_uid(
        "a" * 32,
        "b" * 32,
        "mira-web-test",
        214069,
        mira.Role_Publisher,
        3600,
    )
    assert token.startswith("007")


def test_browser_joins_agora_with_channel_before_token():
    source = (mira.STATIC_DIR / "agora-voice.js").read_text(encoding="utf-8")
    assert "client.join(session.app_id, session.channel, session.token, session.uid)" in source


def test_extract_scan_target_examples():
    expected = {
        "Find the microphone": "microphone",
        "Look for the screwdriver": "screwdriver",
        "Do you see a person wearing a hat?": "person wearing a hat",
        "Can you scan for a red bottle?": "red bottle",
        "Where is the black notebook?": "black notebook",
        "Look for someone holding a phone": "someone holding a phone",
    }
    for command, target in expected.items():
        assert mira.extract_scan_target(command) == target


def test_scan_refuses_identity_and_sensitive_traits_without_touching_hardware():
    fresh_manager()
    for target in ("identify this person", "person's religion", "person with a medical condition", "person's race"):
        with patch.object(mira, "_launch_scan_replay") as launch:
            result = mira.scan_for_target(target)
        assert result["status"] == "refused"
        assert result["found"] is False
        launch.assert_not_called()


class FakeScanProcess:
    def __init__(self):
        self.pid = 9001
        self.stdout = io.StringIO("")
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def test_scan_stops_at_first_match_and_reports_direction():
    manager = fresh_manager()
    process = FakeScanProcess()
    checks = [
        {"found": False, "confidence": "low"},
        {"found": True, "confidence": "high"},
    ]
    with (
        patch.object(mira, "SCAN_CAPTURE_SCHEDULE", ((0, -30), (0, -10), (0, 10), (0, 30))),
        patch.object(mira, "SCAN_REPLAY_DURATION_SECONDS", 0),
        patch.object(mira, "_launch_scan_replay", return_value=process),
        patch.object(mira, "capture_scan_image", side_effect=lambda index, angle: f"/static/scans/scan_{index}_{angle}.jpg"),
        patch.object(mira, "vision_target_check", side_effect=checks),
        patch.object(mira.os, "killpg"),
    ):
        result = mira.scan_for_target("red bottle")
    assert result["found"] is True
    assert result["angle"] == -10
    assert result["confidence"] == "high"
    assert result["message"] == "I found a red bottle about 10 degrees to my left."
    assert manager.get_status()["status"] == "completed"


def test_scan_not_found_checks_all_angles_and_returns_to_completed_state():
    manager = fresh_manager()
    process = FakeScanProcess()
    captures = []
    with (
        patch.object(mira, "SCAN_CAPTURE_SCHEDULE", ((0, -30), (0, -10), (0, 10), (0, 30))),
        patch.object(mira, "SCAN_REPLAY_DURATION_SECONDS", 0),
        patch.object(mira, "_launch_scan_replay", return_value=process),
        patch.object(mira, "capture_scan_image", side_effect=lambda index, angle: captures.append((index, angle)) or f"/static/scans/{index}.jpg"),
        patch.object(mira, "vision_target_check", return_value={"found": False, "confidence": "low"}),
    ):
        result = mira.scan_for_target("black notebook")
    assert captures == [(1, -30), (2, -10), (3, 10), (4, 30)]
    assert result["success"] is True
    assert result["found"] is False
    assert result["confidence"] == "low"
    assert manager.get_status()["history"][0]["skill"] == "scan"


def test_scan_is_rejected_while_robot_is_busy():
    manager = fresh_manager()
    manager.active_skill = "dance"
    with patch.object(mira, "_launch_scan_replay") as launch:
        result = mira.scan_for_target("microphone")
    assert result["status"] == "busy"
    launch.assert_not_called()
