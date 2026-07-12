const connectButton = document.querySelector("#agora-connect-button");
const disconnectButton = document.querySelector("#agora-disconnect-button");
const muteButton = document.querySelector("#agora-mute-button");
const stateElement = document.querySelector("#agora-voice-state");
const messageElement = document.querySelector("#agora-voice-message");

let client = null;
let microphoneTrack = null;
let isMuted = false;

function renderMuteState() {
  muteButton.textContent = isMuted ? "Unmute mic" : "Mute mic";
  muteButton.setAttribute("aria-pressed", String(isMuted));
  muteButton.classList.toggle("muted", isMuted);
}

function setVoiceState(state, message) {
  stateElement.textContent = state;
  stateElement.className = `voice-state ${state.toLowerCase()}`;
  messageElement.textContent = message;
}

async function connectVoice() {
  connectButton.disabled = true;
  setVoiceState("Connecting", "Starting a secure Agora session and requesting microphone access…");
  try {
    const response = await fetch("/api/agora/session/start", { method: "POST" });
    const session = await response.json();
    if (!response.ok || !session.success) throw new Error(session.message || "Agora session could not start.");

    const AgoraRTC = (await import("https://cdn.jsdelivr.net/npm/agora-rtc-sdk-ng@4.24.5/+esm")).default;
    client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
    client.on("user-published", async (user, mediaType) => {
      await client.subscribe(user, mediaType);
      if (mediaType === "audio") user.audioTrack.play();
    });
    client.on("user-unpublished", (user, mediaType) => {
      if (mediaType === "audio") user.audioTrack?.stop();
    });

    // Agora SDK order: appId, channel, token, uid.
    await client.join(session.app_id, session.channel, session.token, session.uid);
    microphoneTrack = await AgoraRTC.createMicrophoneAudioTrack();
    await client.publish([microphoneTrack]);
    disconnectButton.disabled = false;
    muteButton.disabled = false;
    isMuted = false;
    renderMuteState();
    setVoiceState("Connected", "Microphone connected. Speak naturally to Mira.");
  } catch (error) {
    setVoiceState("Error", error.message || "Could not connect to Agora voice.");
    await disconnectVoice();
  } finally {
    connectButton.disabled = Boolean(client);
  }
}

async function disconnectVoice() {
  try {
    microphoneTrack?.stop();
    microphoneTrack?.close();
    if (client) await client.leave();
  } catch (_) {
    // A partial connection may already be closed.
  } finally {
    microphoneTrack = null;
    client = null;
    isMuted = false;
    renderMuteState();
    connectButton.disabled = false;
    muteButton.disabled = true;
    disconnectButton.disabled = true;
    if (!stateElement.classList.contains("error")) setVoiceState("Offline", "Voice session disconnected.");
  }
}

async function toggleMute() {
  if (!microphoneTrack) return;
  try {
    isMuted = !isMuted;
    await microphoneTrack.setEnabled(!isMuted);
    renderMuteState();
    setVoiceState(
      isMuted ? "Muted" : "Connected",
      isMuted ? "Microphone muted. Mira cannot hear background noise." : "Microphone live. Speak naturally to Mira.",
    );
  } catch (error) {
    isMuted = !isMuted;
    renderMuteState();
    setVoiceState("Error", error.message || "Could not change microphone state.");
  }
}

connectButton?.addEventListener("click", connectVoice);
disconnectButton?.addEventListener("click", disconnectVoice);
muteButton?.addEventListener("click", toggleMute);
window.addEventListener("beforeunload", () => { microphoneTrack?.close(); client?.leave(); });
