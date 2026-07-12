# Mira — Voice Physical Agent

Mira turns short voice or text intents into expressive motions on a LeRobot SO-101 follower arm. The gesture system is deliberately independent from the future screwdriver policy: gestures replay existing local datasets, use no cameras, and perform no training.

> **Safety:** Keep hands clear while Mira is moving. Keep the red STOP control visible and test it before a demo.

## Hardware and runtime

- Ubuntu/Linux with a `lerobot` conda environment
- LeRobot checkout at `~/lerobot`
- SO-101 follower on `/dev/ttyACM1`, robot ID `my_follower`
- One Uvicorn worker; process ownership and run history are stored in memory
- Gesture datasets already present under `~/.cache/huggingface/lerobot/local/`

| Skill | Dataset | Episode |
| --- | --- | ---: |
| Wave hello | `local/motion_hi_wave` | 0 |
| Dance | `local/motion_dance` | 0 |
| Play dead | `local/motion_play_dead` | 0 |
| Nod yes | `local/motion_nod_yes` | 0 |
| Shake no | `local/motion_shake_no` | 0 |

Each gesture runs in the background as a camera-free `lerobot-replay` process. Only one robot process may run at a time. Mira captures combined stdout/stderr, monitors the exit code, and records the latest 50 runs. STOP targets the whole process group and escalates from `SIGINT` to `SIGTERM` and then `SIGKILL` if needed.

## Install and run

For a demo, connect both SO-101 USB serial devices and the DSJ-2062-309 wrist camera, then use the hardware-aware launcher:

```bash
cd /home/viz/Downloads/agora/mira
./run_demo.sh
```

The launcher requires ACM0, ACM1, and the configured wrist camera, grants access to their actual device nodes, and starts Uvicorn in the `lerobot` environment. It exits before startup if a cable or device is missing. To install persistent demo permissions once:

```bash
cd /home/viz/Downloads/agora/mira
./install_demo_udev_rules.sh
```

This one-time command requires `sudo`; reconnect the USB devices after it completes. The application itself never runs as root. Override the verified hardware mapping only when necessary:

```bash
ROBOT_PORT=/dev/ttyACM1 LEADER_PORT=/dev/ttyACM0 ./run_demo.sh
```

```bash
cd /home/viz/Downloads/agora/mira
python -m pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. For a phone on the same Wi-Fi network, find the host address with `hostname -I` and open `http://YOUR_LOCAL_IP:8000`.

Do not use `--workers` with Uvicorn. Each worker would own separate robot state and could allow conflicting commands.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/` | Mobile control room |
| `GET` | `/api/status` | Current state, logs, history, and policy availability |
| `POST` | `/api/skill/wave` | Replay wave |
| `POST` | `/api/skill/dance` | Replay dance |
| `POST` | `/api/skill/play_dead` | Replay play-dead motion |
| `POST` | `/api/skill/nod_yes` | Replay yes nod |
| `POST` | `/api/skill/shake_no` | Replay no shake |
| `POST` | `/api/skill/scan_motion` | Replay the recorded 15-second scan motion |
| `POST` | `/api/robot/stop` | Stop the active process group |
| `POST` | `/api/voice/intent` | Map `{ "text": "..." }` to a gesture |
| `POST` | `/api/scan` | Scan for a dynamic visible target using `{ "target": "red bottle" }` |
| `POST` | `/api/agora/tool/scan_for_target` | Agora wrapper for the same visual scan logic |
| `POST` | `/api/skill/screwdriver` | Disabled future-policy endpoint |
| `POST` | `/api/agora/tool/wait_for_motion` | Wait 8 seconds, then report the current motion status to Agora |

Start a gesture:

```bash
curl -X POST http://localhost:8000/api/skill/wave
```

```json
{
  "success": true,
  "message": "Mira is waving hello.",
  "skill": "wave",
  "status": "started"
}
```

Direct skill endpoints return HTTP `409` if Mira is busy. Agora endpoints always return HTTP `200`; a busy tool response is:

```json
{
  "success": false,
  "message": "Mira is already moving. Please wait.",
  "skill": "dance",
  "status": "busy"
}
```

Voice intent mappings include:

- `say hi`, `wave`, `hello`, `greet me` → wave
- `dance`, `celebrate`, `show me a move` → dance
- `play dead`, `act dead`, `sleep` → play dead
- `yes`, `correct`, `can you do that`, `is that possible` → nod yes
- `no`, `not possible`, `unsupported`, `can't do that` → shake no

An unsupported request receives a friendly response and triggers `shake_no` when Mira is idle. It never interrupts an active motion.

## Dynamic vision scan

Mira recognizes commands such as “find the microphone,” “look for the screwdriver,” “do you see a person wearing a hat?”, and “scan for a red bottle.” The same target extraction is available through the text-intent API and Agora's `scan_for_target` custom tool.

Mira replays `local/motion_scan_10s` episode `0` and captures one camera frame at each of the recording's four pauses (approximately 3.0, 5.5, 8.8, and 10.5 seconds). Captures are corrected 90° counterclockwise, written to `static/scans/`, and checked with `gemini-3.1-flash-lite`. The first confident match is saved, but the replay continues through all four positions and completes the full 14.8-second return-to-center motion. Only after completion does the tool return the saved result for Agora to speak. The completion wait deliberately excludes variable `conda` startup time.

Keep the API key server-side in `.env` and never put it in browser JavaScript:

```dotenv
SCAN_CAMERA_PATH=/dev/v4l/by-id/usb-DSJ-250318-J_DSJ-2062-309-video-index0
SCAN_CAMERA_ROTATION=90_ccw
GEMINI_API_KEY=replace-with-a-restricted-key
GEMINI_MODEL=gemini-3.1-flash-lite
```

Only one robot run may be active. STOP interrupts the scan replay, and identity or sensitive-trait targets are refused before camera or robot access.

## Agora Conversational AI

Expose the local service:

```bash
ngrok http 8000
```

Replace `YOUR_NGROK_URL` with the HTTPS forwarding host and configure these tool URLs:

```text
https://YOUR_NGROK_URL/api/agora/tool/run_wave_skill
https://YOUR_NGROK_URL/api/agora/tool/run_dance_skill
https://YOUR_NGROK_URL/api/agora/tool/run_play_dead_skill
https://YOUR_NGROK_URL/api/agora/tool/run_nod_yes_skill
https://YOUR_NGROK_URL/api/agora/tool/run_shake_no_skill
```

All tools use `POST` and accept an empty request body.

Suggested agent prompt:

> You are Mira, a concise voice-controlled physical robot assistant. You can express yourself physically with wave, dance, play dead, nod yes, and shake no. When the user asks you to greet, wave, dance, play dead, nod, or shake no, call the corresponding tool. Keep spoken responses short. Do not claim success before the tool starts. If the robot is busy, say you are already moving.

## Future screwdriver policy

The screwdriver card remains a placeholder and its command is documented in `app.py`. The endpoint does not launch anything unless the server starts with `POLICY_READY=true`:

```bash
POLICY_READY=true uvicorn app:app --host 0.0.0.0 --port 8000
```

The learned policy is separate from gesture replay and retains its own cameras and evaluation dataset.

## Web voice panel

The control room includes a **Talk to Mira** panel. It starts an Agora RTC session server-side, then joins the browser microphone to that temporary channel. The browser only receives a short-lived client token; the App Certificate remains in `.env`.

Set the following local values in `mira/.env` before using it:

```dotenv
AGORA_APP_ID=your_app_id
AGORA_APP_CERTIFICATE=your_rotated_app_certificate
AGORA_PIPELINE_ID=your_agora_pipeline_id
AGORA_AGENT_RTC_UID=214069
```

Restart Uvicorn after changing `.env`. The generated console curl token is not needed and must never be added to the frontend.

## Tests

The regression suite mocks `subprocess.Popen`; it never sends a command to the robot:

```bash
conda run -n lerobot python -m pytest -q
```
