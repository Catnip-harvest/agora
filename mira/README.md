# Mira — Voice-First Physical Workspace Assistant

**Mira** is a feature-rich, voice-first hackathon demo app that maps natural language user intent to a learned physical robot policy controlling a **LeRobot SO-101 follower arm** (`my_follower` on `/dev/ttyACM1`).

Say intent: *"Mira, I need to fix something"* — Mira chooses the physical screwdriver skill (`pick up the screwdriver and put it on the black workspace`) and executes it via `lerobot-record`.

---

## Architecture & Features

- **Voice & Intent Recognition**: Use push-to-talk browser speech recognition (Web Speech API) or type natural language queries.
- **Agentic Conversational Feedback**: Mira speaks back ("I’ll prep the repair workspace and bring the screwdriver over.") before launching physical movement.
- **UI/UX Pro Max Dark Cinematic Design**: Mobile-first touch deck designed for hackathon demonstrations on phones or tablets over local Wi-Fi.
- **Safety First**: Obvious red Emergency Stop button (`POST /api/robot/stop`) with process group cascade termination (`SIGINT` → `SIGTERM` → `SIGKILL`) and visible safety warnings.
- **Agora ConvoAI Tool Scaffold**: Dedicated endpoint (`POST /api/agora/tool/run_screwdriver_skill`) ready for ngrok tunneling to Agora Conversational AI voice agents.

---

## 1. Installation & Running Locally

Install backend dependencies:
```bash
pip install -r requirements.txt
```

Launch the local server:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

---

## 2. Accessing on Laptop & Mobile Phone

1. **Open on Laptop**:
   Visit [http://localhost:8000](http://localhost:8000) in Chrome/Edge/Firefox.
2. **Find your Local LAN IP**:
   ```bash
   hostname -I
   ```
3. **Open on Phone**:
   Connect your phone to the same Wi-Fi network and browse to:
   ```text
   http://YOUR_LOCAL_IP:8000
   ```

---

## 3. Exposing Publicly with ngrok & Connecting Agora ConvoAI

1. **Start ngrok tunnel**:
   ```bash
   ngrok http 8000
   ```
2. **Copy the Public Forwarding URL** (e.g. `https://abc123.ngrok-free.app`).
3. **Configure Agora Conversational AI Tool**:
   In your Agora Agent Tool configuration, set the tool execution URL to:
   ```text
   https://abc123.ngrok-free.app/api/agora/tool/run_screwdriver_skill
   ```
   When your Agora agent decides to run the screwdriver skill, it will POST to this endpoint and receive:
   ```json
   {
     "success": true,
     "message": "I’m moving the screwdriver to the workspace now.",
     "skill": "screwdriver"
   }
   ```

---

## API Reference

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/skill/screwdriver` | Starts trained LeRobot policy subprocess (`outputs/train/smolvla_screwdriver_20k/checkpoints/last/pretrained_model`) |
| `POST` | `/api/robot/stop` | Safely terminates active robot subprocess |
| `GET` | `/api/status` | Returns hardware status, active skill, timestamps, and live logs |
| `POST` | `/api/voice/intent` | Maps NL text (e.g. `"I need to fix something"`) to physical skill |
| `POST` | `/api/agora/tool/run_screwdriver_skill` | Agora ConvoAI JSON tool endpoint |
