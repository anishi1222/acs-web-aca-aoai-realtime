import { CallClient } from "@azure/communication-calling";
import { AzureCommunicationTokenCredential } from "@azure/communication-common";

let callClient;
let callAgent;
let call;
let isCallConnected = false;
let incomingCall;
let currentUserId;
let deviceManager;

const API_BASE = import.meta.env.VITE_API_BASE || "";

async function apiFetch(path, options) {
  const url = `${API_BASE}${path}`;
  return fetch(url, options);
}

const statusEl = document.getElementById("status");
const statusDot = document.getElementById("statusDot");
const wsStateEl = document.getElementById("wsState"); // Reusing this element for Call state
const btnHangup = document.getElementById("btnHangup");
const btnSupplement = document.getElementById("btnSupplement");
const supplementText = document.getElementById("supplementText");

const myUserIdInput = document.getElementById("myUserId");
const btnInit = document.getElementById("btnInit");
const btnServerCall = document.getElementById("btnServerCall");
const btnAccept = document.getElementById("btnAccept");
const incomingStateEl = document.getElementById("incomingState");

function setStatus(text, level = "warn") {
  statusEl.innerText = text;
  // level: ok | warn | bad
  const color = level === "ok" ? "#31d07e" : level === "bad" ? "#ff5c7a" : "#ffcc66";
  statusDot.style.background = color;
  statusDot.style.boxShadow = `0 0 0 4px ${level === "ok" ? "rgba(49,208,126,0.15)" : level === "bad" ? "rgba(255,92,122,0.15)" : "rgba(255,204,102,0.15)"}`;
}

function setCallState(state) {
  wsStateEl.innerText = `Call: ${state}`;
}

function setIncomingState(state) {
  if (!incomingStateEl) return;
  incomingStateEl.innerText = `Incoming: ${state}`;
}

async function initCallAgent() {
  if (callAgent) return;

  try {
    setStatus("fetching token...", "warn");
    const response = await apiFetch("/api/token");
    if (!response.ok) throw new Error("Failed to fetch token");
    const data = await response.json();

    currentUserId = data.userId;
    if (myUserIdInput) myUserIdInput.value = currentUserId;
    
    const credential = new AzureCommunicationTokenCredential(data.token);
    callClient = new CallClient();
    deviceManager = await callClient.getDeviceManager();
    try {
      // Without this, the call can connect but the user may not be able to speak.
      await deviceManager.askDevicePermission({ audio: true });
    } catch (e) {
      console.warn("Mic permission not granted", e);
    }
    callAgent = await callClient.createCallAgent(credential, { displayName: "Web Client" });

    callAgent.on("incomingCall", (args) => {
      incomingCall = args.incomingCall;
      setIncomingState("ringing");
      if (btnAccept) btnAccept.disabled = false;
    });
    
    setStatus("agent ready", "ok");
  } catch (error) {
    console.error(error);
    setStatus("token error", "bad");
    throw error;
  }
}

async function acceptIncoming() {
  if (!incomingCall) {
    alert("No incoming call");
    return;
  }
  try {
    setStatus("accepting...", "warn");
    // Best-effort: accept with audio enabled.
    call = await incomingCall.accept();
    incomingCall = null;
    if (btnAccept) btnAccept.disabled = true;
    setIncomingState("accepted");

    call.on("stateChanged", () => {
      setCallState(call.state);
      if (call.state === "Connected") {
        setStatus("connected", "ok");
        isCallConnected = true;
        try {
          // Ensure we are not muted on connect.
          if (typeof call.isMuted === "boolean" ? call.isMuted : false) {
            call.unmute();
          }
        } catch {
          // Ignore if not supported.
        }
        btnHangup.disabled = false;
        if (btnServerCall) btnServerCall.disabled = true;
      } else if (call.state === "Disconnected") {
        setStatus("disconnected", "warn");
        isCallConnected = false;
        btnHangup.disabled = true;
        if (btnServerCall) btnServerCall.disabled = false;
        call = null;
        setIncomingState("none");
      }
    });
  } catch (e) {
    console.error(e);
    setStatus("accept failed", "bad");
  }
}

async function serverStartCall() {
  try {
    await initCallAgent();
    if (!currentUserId) throw new Error("No userId");

    setStatus("requesting server call...", "warn");
    const resp = await apiFetch("/api/call/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        targetUserId: currentUserId,
        sourceDisplayName: "Realtime Server",
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      console.error(data);
      throw new Error(data.error || "Server call failed");
    }
    setIncomingState("waiting...");
    setStatus("server started call", "ok");
  } catch (e) {
    console.error(e);
    setStatus("server call failed", "bad");
    alert(String(e.message || e));
  }
}

async function hangUp() {
  if (call) {
    await call.hangUp();
    call = null;
  }
  isCallConnected = false;
  btnHangup.disabled = true;
  if (btnServerCall) btnServerCall.disabled = false;
  if (btnAccept) btnAccept.disabled = true;
  setIncomingState("none");
  setStatus("stopped", "warn");
}
btnHangup.onclick = hangUp;

if (btnInit) {
  btnInit.onclick = async () => {
    setIncomingState("none");
    await initCallAgent();
    if (btnServerCall) btnServerCall.disabled = false;
  };
}

if (btnServerCall) {
  btnServerCall.onclick = serverStartCall;
}

if (btnAccept) {
  btnAccept.onclick = acceptIncoming;
}

// Init UI state
setCallState("idle");
setStatus("idle", "warn");
btnHangup.disabled = true;
setIncomingState("none");
if (btnServerCall) btnServerCall.disabled = true;
if (btnAccept) btnAccept.disabled = true;

// Supplement text handling (optional, if we want to send text via some other means)
btnSupplement.onclick = async () => {
  const text = supplementText.value.trim();
  if (!text) return;
  console.log("Supplement text:", text);
  supplementText.value = "";
};
