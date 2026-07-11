/* ═══════════════════════════════════════════════════════════════
   Mira — Voice-First Physical Workspace Assistant
   Frontend JavaScript Engine · Voice Recognition & TTS · 600ms Polling
   ═══════════════════════════════════════════════════════════════ */

let recognition = null;
let isListening = false;
let pollingTimer = null;
let lastLogCount = 0;

const $ = (id) => document.getElementById(id);

// Initialize Speech Recognition & Polling on load
document.addEventListener("DOMContentLoaded", () => {
    initSpeechRecognition();
    pollingTimer = setInterval(pollBackendStatus, 600);
    pollBackendStatus();

    // Enter key handler for input box
    const intentInput = $("intent-input");
    if (intentInput) {
        intentInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") submitTextIntent();
        });
    }
});

/* ─── Web Speech API Recognition ───────────────────────────── */
function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        console.warn("Speech recognition not supported in this browser.");
        const micLabel = $("mic-label");
        if (micLabel) micLabel.textContent = "Mic N/A";
        return;
    }

    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onstart = () => {
        isListening = true;
        const micBtn = $("mic-btn");
        const waves = $("listening-waves");
        const label = $("mic-label");
        if (micBtn) micBtn.classList.add("listening");
        if (waves) waves.classList.remove("hidden");
        if (label) label.textContent = "Listening...";
        showToast("Listening... Speak your intent", "info");
    };

    recognition.onresult = (event) => {
        let interimText = "";
        let finalInputText = "";
        for (let i = event.resultIndex; i < event.results.length; ++i) {
            if (event.results[i].isFinal) {
                finalInputText += event.results[i][0].transcript;
            } else {
                interimText += event.results[i][0].transcript;
            }
        }
        const transcriptEl = $("transcript-live");
        if (transcriptEl) transcriptEl.textContent = finalInputText || interimText;

        if (finalInputText.trim().length > 0) {
            sendIntentToBackend(finalInputText.trim());
        }
    };

    recognition.onerror = (event) => {
        console.error("Speech error:", event.error);
        stopSpeechRecognition();
    };

    recognition.onend = () => {
        stopSpeechRecognition();
    };
}

function toggleSpeechRecognition() {
    if (!recognition) {
        showToast("Voice recognition is not supported in this browser. Try Chrome/Edge or type below.", "error");
        return;
    }
    if (isListening) {
        recognition.stop();
    } else {
        $("transcript-live").textContent = "";
        try {
            recognition.start();
        } catch (err) {
            console.error("Mic start err:", err);
        }
    }
}

function stopSpeechRecognition() {
    isListening = false;
    const micBtn = $("mic-btn");
    const waves = $("listening-waves");
    const label = $("mic-label");
    if (micBtn) micBtn.classList.remove("listening");
    if (waves) waves.classList.add("hidden");
    if (label) label.textContent = "Push to Talk";
}

/* ─── Speech Synthesis (TTS Response) ──────────────────────── */
function speakAssistantMessage(text) {
    const ttsToggle = $("voice-tts-toggle");
    if (!ttsToggle || !ttsToggle.checked) return;
    if (!("speechSynthesis" in window)) return;

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.02;
    window.speechSynthesis.speak(utterance);
}

/* ─── Intent & Voice Submission ────────────────────────────── */
async function submitTextIntent() {
    const input = $("intent-input");
    const text = input ? input.value.trim() : "";
    if (!text) return;
    input.value = "";
    await sendIntentToBackend(text);
}

function triggerDemoPhrase(phrase) {
    const input = $("intent-input");
    if (input) input.value = phrase;
    sendIntentToBackend(phrase);
}

async function sendIntentToBackend(text) {
    showToast(`Intent: "${text}"`, "info");
    try {
        const res = await fetch("/api/voice/intent", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
        });
        const data = await res.json();
        updateAssistantBubble(data.message);
        speakAssistantMessage(data.message);
        pollBackendStatus();
    } catch (err) {
        showToast("Error sending voice intent to server", "error");
    }
}

/* ─── Direct Skill & Stop Triggers ─────────────────────────── */
async function triggerSkill(skillName) {
    try {
        const res = await fetch("/api/skill/screwdriver", {
            method: "POST"
        });
        const data = await res.json();
        if (res.ok) {
            showToast("Screwdriver policy started", "success");
            updateAssistantBubble(data.message || "I’ll prep the repair workspace and bring the screwdriver over.");
            speakAssistantMessage(data.message || "I’ll prep the repair workspace and bring the screwdriver over.");
        } else {
            showToast(data.error || "Could not start run", "error");
        }
        pollBackendStatus();
    } catch (err) {
        showToast("Error calling skill endpoint", "error");
    }
}

async function emergencyStop() {
    try {
        const res = await fetch("/api/robot/stop", {
            method: "POST"
        });
        const data = await res.json();
        showToast(data.message || "Robot stopped", "info");
        pollBackendStatus();
    } catch (err) {
        showToast("Error calling stop endpoint", "error");
    }
}

async function fetchStatusManual() {
    showToast("Polling hardware status...", "info");
    await pollBackendStatus();
}

/* ─── 600ms Status Polling ─────────────────────────────────── */
async function pollBackendStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) return;
        const data = await res.json();

        // Update status pill
        const pill = $("status-pill");
        const statusText = $("status-text");
        if (pill) {
            pill.className = `status-pill status-${data.robot_status}`;
        }
        if (statusText) {
            statusText.textContent = (data.robot_status || "IDLE").toUpperCase();
        }

        // Active skill pill
        const skillLabel = $("active-skill-label");
        if (skillLabel) {
            if (data.active_skill) {
                skillLabel.textContent = `Active: ${data.active_skill.toUpperCase()}`;
                skillLabel.classList.remove("hidden");
            } else {
                skillLabel.classList.add("hidden");
            }
        }

        // Assistant bubble
        if (data.last_assistant_message) {
            updateAssistantBubble(data.last_assistant_message);
        }

        // Update logs
        if (data.latest_logs && data.latest_logs.length !== lastLogCount) {
            renderLogs(data.latest_logs);
            lastLogCount = data.latest_logs.length;
        }
    } catch (err) {
        // Silent catch during periodic polling
    }
}

function updateAssistantBubble(msg) {
    const bubble = $("assistant-bubble");
    if (bubble && bubble.textContent.trim() !== msg) {
        bubble.textContent = msg;
    }
}

/* ─── Log Panel Handling ───────────────────────────────────── */
function renderLogs(logs) {
    const logContent = $("log-content");
    const logBadge = $("log-count-badge");
    if (!logContent) return;

    if (logBadge) logBadge.textContent = `${logs.length} lines`;

    logContent.innerHTML = logs.map(l => {
        let typeClass = "log-info";
        if (l.text.includes("✅")) typeClass = "log-success";
        else if (l.text.includes("❌") || l.text.includes("⛔")) typeClass = "log-error";
        else if (l.text.includes("🚀") || l.text.includes("📋")) typeClass = "log-command";

        return `
            <div class="log-line ${typeClass}">
                <span class="log-time">${l.time || ""}</span>
                <span class="log-text">${escapeHtml(l.text)}</span>
            </div>
        `;
    }).join("");

    const container = $("logs-container");
    if (container) container.scrollTop = container.scrollHeight;
}

function toggleLogsPanel() {
    const container = $("logs-container");
    const chevron = $("logs-chevron");
    if (container) container.classList.toggle("hidden");
    if (chevron) chevron.classList.toggle("collapsed");
}

function clearLogs(e) {
    if (e) e.stopPropagation();
    const logContent = $("log-content");
    if (logContent) logContent.innerHTML = "";
    lastLogCount = 0;
    showToast("Logs cleared", "info");
}

/* ─── Agora Integration Helpers ────────────────────────────── */
function copyAgoraEndpoint() {
    const url = "POST /api/agora/tool/run_screwdriver_skill";
    navigator.clipboard.writeText(url)
        .then(() => showToast("Copied Agora endpoint path!", "success"))
        .catch(() => showToast("Failed to copy", "error"));
}

/* ─── Toast System ─────────────────────────────────────────── */
function showToast(message, type = "info") {
    const container = $("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 3000);
}

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
