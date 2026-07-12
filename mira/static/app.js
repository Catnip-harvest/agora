"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const POLL_INTERVAL_MS = 800;

let recognition = null;
let pollInFlight = false;
let isListening = false;
let lastStatus = null;
let lastHistoryFingerprint = "";
let lastLogFingerprint = "";

document.addEventListener("DOMContentLoaded", () => {
  $("#intent-form").addEventListener("submit", submitIntent);
  $("#intent-form").addEventListener("click", (event) => {
    if (!event.target.closest("button")) $("#intent-input").focus();
  });
  $("#scan-form").addEventListener("submit", submitScan);
  $$("[data-skill]").forEach((button) => button.addEventListener("click", () => runSkill(button.dataset.skill)));
  $$("[data-scan-example]").forEach((button) => button.addEventListener("click", () => {
    $("#scan-target-input").value = button.dataset.scanExample;
  }));
  $$("[data-stop]").forEach((button) => button.addEventListener("click", stopMira));
  $("#logs-toggle").addEventListener("click", toggleLogs);
  initSpeechRecognition();
  pollStatus();
  window.setInterval(pollStatus, POLL_INTERVAL_MS);
});

async function apiRequest(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`Mira returned an invalid response (${response.status}).`); }
  return { response, data };
}

async function runSkill(skill) {
  setControlsBusy(true);
  try {
    const { response, data } = await apiRequest(`/api/skill/${skill}`, { method: "POST" });
    handleActionResponse(response, data);
  } catch (error) {
    showToast(error.message || "Could not reach Mira.", "error");
  } finally {
    await pollStatus();
  }
}

async function submitIntent(event) {
  event.preventDefault();
  const input = $("#intent-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  $("#send-button").disabled = true;
  try {
    const { response, data } = await apiRequest("/api/voice/intent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    handleActionResponse(response, data);
    if (Object.hasOwn(data, "found")) speakResult(data.message);
  } catch (error) {
    showToast(error.message || "Could not send the intent.", "error");
  } finally {
    $("#send-button").disabled = false;
    await pollStatus();
  }
}

async function submitScan(event) {
  event.preventDefault();
  const input = $("#scan-target-input");
  const target = input.value.trim();
  if (!target) return;
  $("#scan-button").disabled = true;
  try {
    const { response, data } = await apiRequest("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
    });
    handleActionResponse(response, data);
    renderScan({ ...data, message: data.message });
    speakResult(data.message);
  } catch (error) {
    showToast(error.message || "Could not run the visual scan.", "error");
  } finally {
    $("#scan-button").disabled = false;
    await pollStatus();
  }
}

function speakResult(message) {
  if (!message || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(message);
  utterance.lang = "en-US";
  utterance.rate = 0.96;
  window.speechSynthesis.speak(utterance);
}

async function stopMira() {
  try {
    const { data } = await apiRequest("/api/robot/stop", { method: "POST" });
    updateAssistant(data.message);
    showToast(data.message, data.success ? "success" : "info");
  } catch (error) {
    showToast(error.message || "STOP could not reach Mira.", "error");
  } finally {
    await pollStatus();
  }
}

function handleActionResponse(response, data) {
  updateAssistant(data.message || "Mira received the command.");
  const type = data.success ? "success" : (response.ok && data.status !== "failed" ? "info" : "error");
  showToast(data.message || "Command received.", type);
}

async function pollStatus() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const { response, data } = await apiRequest("/api/status");
    if (!response.ok) throw new Error("Status unavailable.");
    renderStatus(data);
    const connection = $("#connection-state");
    connection.textContent = "ONLINE";
    connection.classList.add("online");
  } catch {
    const connection = $("#connection-state");
    connection.textContent = "OFFLINE";
    connection.classList.remove("online");
  } finally {
    pollInFlight = false;
  }
}

function renderStatus(data) {
  const status = data.status || data.robot_status || "idle";
  const pill = $("#status-pill");
  pill.className = `status-pill status-${status}`;
  $("#status-text").textContent = displayName(status);

  const active = $("#active-skill");
  if (data.active_skill) {
    active.textContent = `Running · ${displayName(data.active_skill)}`;
    active.classList.remove("hidden");
  } else {
    active.classList.add("hidden");
  }

  setControlsBusy(status === "running" || (status === "stopped" && Boolean(data.active_skill)));
  updateAssistant(data.message);
  renderLogs(data.latest_logs || []);
  renderHistory(data.history || []);
  renderScrewdriver(data.screwdriver_available);
  renderScan(data.scan || {});

  if (lastStatus && lastStatus === "running" && ["completed", "failed", "stopped"].includes(status)) {
    showToast(data.message, status === "completed" ? "success" : (status === "failed" ? "error" : "info"));
  }
  lastStatus = status;
}

function setControlsBusy(isBusy) {
  $$("[data-skill]").forEach((button) => { button.disabled = isBusy; });
  $("#scan-button").disabled = isBusy;
}

function updateAssistant(message) {
  if (!message) return;
  const paragraph = $("#assistant-message p");
  if (paragraph.textContent !== message) paragraph.textContent = message;
}

function renderLogs(logs) {
  const fingerprint = logs.length ? `${logs.length}:${logs[logs.length - 1].index}` : "0";
  if (fingerprint === lastLogFingerprint) return;
  lastLogFingerprint = fingerprint;
  $("#log-count").textContent = logs.length;
  const list = $("#log-list");
  list.replaceChildren();
  if (!logs.length) {
    list.append(emptyMessage("Waiting for a robot command…"));
    return;
  }
  logs.forEach((log) => {
    const row = document.createElement("div");
    row.className = "log-line";
    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = log.time || "--:--:--";
    const text = document.createElement("span");
    text.className = "log-text";
    text.textContent = log.text || "";
    row.append(time, text);
    list.append(row);
  });
  list.scrollTop = list.scrollHeight;
}

function renderHistory(history) {
  const fingerprint = history.map((run) => `${run.run_id}:${run.status}:${run.exit_code}`).join("|");
  if (fingerprint === lastHistoryFingerprint) return;
  lastHistoryFingerprint = fingerprint;
  $("#history-count").textContent = `${history.length} ${history.length === 1 ? "run" : "runs"}`;
  const list = $("#history-list");
  list.replaceChildren();
  if (!history.length) {
    list.append(emptyMessage("Completed motions will appear here."));
    return;
  }
  history.forEach((run) => {
    const item = document.createElement("article");
    item.className = "history-item";
    const top = document.createElement("div");
    top.className = "history-top";
    const skill = document.createElement("span");
    skill.className = "history-skill";
    skill.textContent = displayName(run.skill);
    const status = document.createElement("span");
    status.className = "history-status";
    status.textContent = run.status;
    top.append(skill, status);
    const meta = document.createElement("div");
    meta.className = "history-meta";
    const start = formatDate(run.started_at);
    const finish = formatDate(run.finished_at);
    meta.textContent = `${start} → ${finish} · exit ${run.exit_code ?? "n/a"}`;
    item.append(top, meta);
    list.append(item);
  });
}

function renderScrewdriver(available) {
  const card = $("#screwdriver-card");
  const copy = $("#screwdriver-copy");
  copy.textContent = available ? "Policy ready via API" : "Learned policy coming soon";
  card.classList.toggle("policy-ready", Boolean(available));
}

function renderScan(scan) {
  if (!scan || !scan.target) return;
  $("#scan-current-target").textContent = scan.target;
  $("#scan-current-angle").textContent = scan.angle === null || scan.angle === undefined ? "—" : `${scan.angle}°`;
  const result = scan.found === true ? "Found" : (scan.found === false ? "Not found" : "Scanning");
  $("#scan-current-result").textContent = scan.confidence ? `${result} · ${scan.confidence}` : result;
  $("#scan-current-message").textContent = scan.message || "Scanning…";
  const badge = $("#scan-result-badge");
  badge.textContent = result;
  badge.className = `voice-state ${scan.found === true ? "connected" : (scan.found === false ? "error" : "connecting")}`;
  if (scan.image_path) {
    const image = $("#scan-image");
    image.src = `${scan.image_path}?v=${Date.now()}`;
    image.classList.remove("hidden");
    $("#scan-image-empty").classList.add("hidden");
  }
}

function toggleLogs() {
  const toggle = $("#logs-toggle");
  const body = $("#logs-body");
  const expanded = toggle.getAttribute("aria-expanded") === "true";
  toggle.setAttribute("aria-expanded", String(!expanded));
  body.classList.toggle("closed", expanded);
}

function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;
  const button = $("#mic-button");
  button.classList.remove("hidden");
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  recognition.onstart = () => setListening(true);
  recognition.onend = () => setListening(false);
  recognition.onerror = (event) => {
    setListening(false);
    if (event.error === "network") {
      showToast("Browser transcription is unavailable. Type your request or use Connect voice below.", "error");
      $("#intent-input").focus();
    } else if (event.error !== "aborted") {
      showToast(`Microphone: ${event.error}`, "error");
    }
  };
  recognition.onresult = (event) => {
    let transcript = "";
    let isFinal = false;
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      transcript += event.results[index][0].transcript;
      isFinal ||= event.results[index].isFinal;
    }
    $("#intent-input").value = transcript.trim();
    if (isFinal && transcript.trim()) $("#intent-form").requestSubmit();
  };
  button.addEventListener("click", () => {
    if (isListening) recognition.stop();
    else recognition.start();
  });
}

function setListening(value) {
  isListening = value;
  $("#mic-button").classList.toggle("listening", value);
  $("#speech-status").classList.toggle("hidden", !value);
}

function displayName(value = "") {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function emptyMessage(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "empty-state";
  paragraph.textContent = text;
  return paragraph;
}

function showToast(message, type = "info") {
  if (!message) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 3600);
}
