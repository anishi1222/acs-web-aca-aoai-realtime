import asyncio
import base64
import json
import os
import sys
import traceback
import time
from dataclasses import dataclass, field

import websockets

try:
  import numpy as np  # type: ignore
except Exception:  # pragma: no cover
  np = None  # type: ignore

try:
  import soxr  # type: ignore
except Exception:  # pragma: no cover
  soxr = None  # type: ignore

try:
  import audioop  # stdlib (deprecated in newer Python, still present in 3.12)
except Exception:  # pragma: no cover
  audioop = None  # type: ignore

try:
# Ensure `server/` is importable when running as `python scripts/xxx.py`.
# (sys.path[0] becomes `server/scripts`, so sibling modules like `aoai_realtime.py`
# in `server/` are not found unless we add the parent directory.)
  SERVER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
  if SERVER_ROOT not in sys.path:
    sys.path.insert(0, SERVER_ROOT)

  from aoai_realtime import AOAIRealtime
  _AOAI_IMPORT_ERROR = None
except Exception as e:
  AOAIRealtime = None  # type: ignore
  _AOAI_IMPORT_ERROR = {"error": repr(e), "trace": traceback.format_exc()}

try:
  from foundry_agent import FoundryWebGroundingAgent
except Exception:
  FoundryWebGroundingAgent = None  # type: ignore

HOST = os.getenv("MEDIA_WS_HOST", "0.0.0.0")
PORT = int(os.getenv("MEDIA_WS_PORT", "8765"))

def _env_bool(name: str, default: bool) -> bool:
  v = os.getenv(name)
  if v is None:
    return default
  s = v.strip().lower()
  if s in ("1", "true", "t", "yes", "y", "on"):
    return True
  if s in ("0", "false", "f", "no", "n", "off"):
    return False
  return default


# If not explicitly set, enable AOAI when config looks present.
_AOAI_CONFIG_PRESENT = bool(os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT"))
ENABLE_AOAI = _env_bool("MEDIA_WS_ENABLE_AOAI", _AOAI_CONFIG_PRESENT)

# AOAI Realtime in this repo is configured for 24kHz PCM16.
AOAI_TARGET_RATE = int(os.getenv("MEDIA_WS_AOAI_TARGET_RATE", "24000"))
AOAI_AUTO_CREATE_RESPONSE = os.getenv("MEDIA_WS_AOAI_AUTO_CREATE_RESPONSE", "1").strip().lower() in (
  "1",
  "true",
  "yes",
  "y",
  "on",
)
AOAI_RESPONSE_FALLBACK_DELAY_MS = int(os.getenv("MEDIA_WS_AOAI_RESPONSE_FALLBACK_DELAY_MS", "600"))

# If bidirectional media streaming is enabled in ACS, forward AOAI audio back to the call.
ACS_SEND_AUDIO = os.getenv("MEDIA_WS_SEND_AUDIO_TO_ACS", "1").strip().lower() in (
  "1",
  "true",
  "yes",
  "y",
  "on",
)
ACS_SEND_MIN_CHUNK_BYTES = int(os.getenv("MEDIA_WS_ACS_SEND_MIN_CHUNK_BYTES", "3200"))  # ~100ms @ 16kHz mono PCM16
ACS_SEND_FLUSH_ON_DONE = os.getenv("MEDIA_WS_ACS_SEND_FLUSH_ON_DONE", "1").strip().lower() in (
  "1",
  "true",
  "yes",
  "y",
  "on",
)

# Debug logging
LOG_AUDIO_STATS = _env_bool("MEDIA_WS_LOG_AUDIO_STATS", False)
LOG_AUDIO_STATS_INTERVAL_MS = int(os.getenv("MEDIA_WS_LOG_AUDIO_STATS_INTERVAL_MS", "2000"))

# Debug: log assistant output transcript (when the Realtime service emits transcript events).
# Useful for validating what the assistant said even when output modality is audio.
LOG_AOAI_OUTPUT_TRANSCRIPT = _env_bool("MEDIA_WS_LOG_AOAI_OUTPUT_TRANSCRIPT", True)

# Foundry Agent (Web grounding)
_AGENT_CONFIG_PRESENT = bool(os.getenv("AZURE_AI_PROJECT_ENDPOINT") and os.getenv("AZURE_AI_AGENT_ID")) or bool(
  os.getenv("AZURE_FOUNDRY_PROJECT_ENDPOINT") and os.getenv("AZURE_FOUNDRY_AGENT_ID")
)
ENABLE_AGENT = _env_bool("MEDIA_WS_AGENT_ENABLE", _AGENT_CONFIG_PRESENT)
AGENT_TIMEOUT_MS = int(os.getenv("MEDIA_WS_AGENT_TIMEOUT_MS", "2000"))
AGENT_FAILURE_PREFIX = os.getenv("MEDIA_WS_AGENT_FALLBACK_PREFIX", "今は検索できないので一般知識で答えます")

# Barge-in: if the user's speech contains these phrases, cancel the current AOAI response.
# Comma-separated list. Default is intentionally narrow to avoid false positives.
_DEFAULT_BARGE_IN_PHRASES = "ちょっと待って,ちょっとまって"
BARGE_IN_PHRASES = [
  s.strip() for s in os.getenv("MEDIA_WS_BARGE_IN_PHRASES", _DEFAULT_BARGE_IN_PHRASES).split(",") if s.strip()
]
BARGE_IN_DROP_MS = int(os.getenv("MEDIA_WS_BARGE_IN_DROP_MS", "1500"))

# Barge-in (immediate): cancel the assistant as soon as the user starts speaking (VAD speech_started).
# Default ON to make interruption feel immediate.
BARGE_IN_ON_SPEECH_STARTED = _env_bool("MEDIA_WS_BARGE_IN_ON_SPEECH_STARTED", True)

# Resampling method used when sample rates differ.
# - auto: prefer soxr (high quality) when installed, else audioop
# - soxr: require soxr, else drop audio (empty)
# - audioop: require audioop, else drop audio (empty)
RESAMPLER = os.getenv("MEDIA_WS_RESAMPLER", "soxr").strip().lower()
SOXR_QUALITY = os.getenv("MEDIA_WS_SOXR_QUALITY", "HQ").strip()  # e.g. LQ/MQ/HQ/VHQ


def _log_audio_config():
  print(
    "Audio config",
    {
      "resampler": RESAMPLER,
      "soxrAvailable": bool(soxr is not None and np is not None),
      "soxrQuality": SOXR_QUALITY,
      "audioopAvailable": bool(audioop is not None),
      "aoaiTargetRate": AOAI_TARGET_RATE,
      "acsSendMinChunkBytes": ACS_SEND_MIN_CHUNK_BYTES,
      "acsSendFlushOnDone": ACS_SEND_FLUSH_ON_DONE,
      "logAudioStats": LOG_AUDIO_STATS,
      "logAudioStatsIntervalMs": LOG_AUDIO_STATS_INTERVAL_MS,
      "logAoaiOutputTranscript": LOG_AOAI_OUTPUT_TRANSCRIPT,
      "bargeInPhrases": BARGE_IN_PHRASES,
      "bargeInDropMs": BARGE_IN_DROP_MS,
      "bargeInOnSpeechStarted": BARGE_IN_ON_SPEECH_STARTED,
    },
  )


def _safe_json(text: str):
  try:
    return json.loads(text)
  except Exception:
    return None


def _now_ms() -> int:
  return int(time.time() * 1000)


@dataclass
class StreamState:
  call_connection_id: str | None
  corr_id: str | None
  sample_rate: int | None = None
  channels: int | None = None
  encoding: str | None = None
  bytes_in: int = 0
  last_stat_ms: int = 0
  aoai: object | None = None
  aoai_ready: asyncio.Event = field(default_factory=asyncio.Event)
  aoai_rate_state: object | None = None
  aoai_inflight: bool = False
  aoai_pending_commit_task: asyncio.Task | None = None
  aoai_pump_task: asyncio.Task | None = None
  aoai_supervisor_task: asyncio.Task | None = None
  aoai_to_acs_rate_state: object | None = None
  aoai_out_buf: bytearray = field(default_factory=bytearray)
  drop_aoai_audio_until_ms: int = 0
  aoai_out_transcript_buf: list[str] = field(default_factory=list)
  closed: asyncio.Event = field(default_factory=asyncio.Event)
  agent: object | None = None
  agent_inflight: bool = False
  agent_last_query_ms: int = 0


def _normalize_jp(text: str) -> str:
  # Minimal normalization for Japanese trigger phrases.
  return "".join((text or "").strip().split())


def _is_barge_in(text: str) -> bool:
  if not text:
    return False
  t = _normalize_jp(text)
  if not t:
    return False
  for phrase in BARGE_IN_PHRASES:
    p = _normalize_jp(phrase)
    if p and p in t:
      return True
  return False


def _extract_transcript_text(ev: dict) -> str | None:
  raw = ev.get("transcript")
  if isinstance(raw, str):
    return raw
  if isinstance(raw, dict):
    for k in ("text", "transcript", "value", "content"):
      v = raw.get(k)
      if isinstance(v, str) and v.strip():
        return v
  v = ev.get("text")
  if isinstance(v, str) and v.strip():
    return v
  return None


def _extract_text_delta(ev: dict) -> str | None:
  # Many Realtime events use one of these keys for incremental text.
  for k in ("delta", "text", "transcript", "content"):
    v = ev.get(k)
    if isinstance(v, str) and v:
      return v
  return None


def _resample_pcm16_mono(
  pcm: bytes,
  *,
  src_rate: int,
  dst_rate: int,
  state: object | None,
  final: bool = False,
):
  if not pcm:
    # Allow flushing stateful resamplers at end-of-stream.
    if not final:
      return b"", state
    if soxr is not None and np is not None and isinstance(state, dict) and state.get("kind") == "soxr":
      try:
        stream = state.get("stream")
        if stream is None:
          return b"", None
        y = stream.resample_chunk(np.zeros((0,), dtype=np.float32), last=True)
        if y is None or len(y) == 0:
          return b"", None
        y16 = np.clip(y * 32768.0, -32768.0, 32767.0).astype(np.int16)
        return y16.tobytes(), None
      except Exception:
        return b"", None
    # audioop doesn't have an explicit flush mechanism we rely on here.
    return b"", None
  if src_rate == dst_rate:
    return pcm, state

  want_soxr = RESAMPLER in ("auto", "soxr")
  want_audioop = RESAMPLER in ("auto", "audioop")

  # Prefer soxr when available (better quality than audioop.ratecv for downsampling).
  # Use a stateful resampler to avoid chunk-boundary artifacts.
  if want_soxr and soxr is not None and np is not None:
    # Ensure we have whole samples (2 bytes/sample).
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    if not pcm:
      return b"", None
    try:
      x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

      soxr_state: dict | None = state if isinstance(state, dict) and state.get("kind") == "soxr" else None
      if (
        soxr_state is None
        or soxr_state.get("src_rate") != src_rate
        or soxr_state.get("dst_rate") != dst_rate
        or soxr_state.get("quality") != SOXR_QUALITY
        or soxr_state.get("stream") is None
      ):
        stream = soxr.ResampleStream(src_rate, dst_rate, 1, dtype="float32", quality=SOXR_QUALITY)
        soxr_state = {
          "kind": "soxr",
          "src_rate": src_rate,
          "dst_rate": dst_rate,
          "quality": SOXR_QUALITY,
          "stream": stream,
        }

      stream = soxr_state["stream"]
      y = stream.resample_chunk(x, last=final)
      y16 = np.clip(y * 32768.0, -32768.0, 32767.0).astype(np.int16)
      return y16.tobytes(), soxr_state
    except Exception:
      # Fall back to audioop if allowed.
      if RESAMPLER == "soxr":
        return b"", None

  if want_audioop and audioop is not None:
    # width=2 bytes, channels=1
    converted, new_state = audioop.ratecv(pcm, 2, 1, src_rate, dst_rate, state)
    return converted, new_state

  # No resampler available.
  return b"", state


def _downmix_pcm16_stereo_to_mono(pcm: bytes) -> bytes:
  if audioop is None:
    return b""
  try:
    return audioop.tomono(pcm, 2, 0.5, 0.5)
  except Exception:
    return b""


async def _connect_aoai(state: StreamState):
  if AOAIRealtime is None:
    if _AOAI_IMPORT_ERROR:
      print(
        "AOAIRealtime import failed; skipping",
        {"callConnectionId": state.call_connection_id, **_AOAI_IMPORT_ERROR},
      )
    else:
      print("AOAIRealtime not available; skipping", {"callConnectionId": state.call_connection_id})
    state.aoai = None
    state.aoai_ready.set()
    return

  try:
    rt = AOAIRealtime()
    await rt.connect()
    state.aoai = rt
    print(
      "AOAI connected",
      {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id, "ts": _now_ms()},
    )
  except Exception as e:
    print(
      "AOAI connect failed",
      {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id, "error": repr(e)},
    )
    state.aoai = None
  finally:
    state.aoai_ready.set()


async def _aoai_supervisor(state: StreamState):
  """Keep AOAI realtime connected; retry without dropping the call.

  This loop is best-effort. It keeps trying to connect and run the pump until the ACS WS closes.
  """
  backoff_ms = 500
  while not state.closed.is_set():
    # If we already have a live pump, just wait.
    if state.aoai is not None and state.aoai_pump_task is not None and not state.aoai_pump_task.done():
      try:
        await asyncio.wait_for(state.closed.wait(), timeout=1.0)
      except Exception:
        pass
      continue

    # Reset readiness for a fresh attempt.
    state.aoai_ready.clear()
    await _connect_aoai(state)

    rt = state.aoai
    if rt is None:
      # Retry with backoff.
      try:
        await asyncio.wait_for(state.closed.wait(), timeout=backoff_ms / 1000.0)
        continue
      except Exception:
        pass
      backoff_ms = min(int(backoff_ms * 1.8), 8000)
      continue

    # Connected: start pump, then wait until it ends (or socket closes).
    try:
      state.aoai_pump_task = asyncio.create_task(_aoai_pump(state))
      await asyncio.wait(
        [state.aoai_pump_task, asyncio.create_task(state.closed.wait())],
        return_when=asyncio.FIRST_COMPLETED,
      )
    finally:
      # Ensure ws is closed so the next loop does a clean reconnect.
      try:
        await rt.close()
      except Exception:
        pass
      state.aoai = None
      backoff_ms = 500


async def _ensure_agent(state: StreamState):
  if not ENABLE_AGENT:
    state.agent = None
    return
  if state.agent is not None:
    return
  if FoundryWebGroundingAgent is None:
    state.agent = None
    print(
      "Foundry agent SDK not available; grounding disabled",
      {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id},
    )
    return
  try:
    state.agent = FoundryWebGroundingAgent()
  except Exception as e:
    state.agent = None
    print(
      "Failed to init Foundry agent; grounding disabled",
      {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id, "error": repr(e)},
    )


async def _agent_answer_or_fallback(*, state: StreamState, rt: AOAIRealtime, transcript: str):
  """Call Foundry agent (web-grounded) and force the realtime assistant to speak the result.

  On failure/timeout, trigger a non-grounded response with the required prefix.
  """
  if not transcript.strip():
    return

  if not ENABLE_AGENT:
    return

  # Avoid overlapping agent calls; keep the call responsive.
  if state.agent_inflight:
    return

  state.agent_inflight = True
  state.agent_last_query_ms = _now_ms()
  await _ensure_agent(state)

  try:
    agent = state.agent
    result_text = None
    if agent is not None:
      try:
        result_text = await asyncio.wait_for(
          agent.run(
            query=transcript,
            correlation={"callConnectionId": state.call_connection_id, "correlationId": state.corr_id},
          ),
          timeout=max(0.1, AGENT_TIMEOUT_MS / 1000.0),
        )
      except Exception:
        result_text = None

    if result_text and isinstance(result_text, str) and result_text.strip():
      print(
        "Foundry grounded answer",
        {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id, "chars": len(result_text)},
      )
      # Force speaking the grounded answer as-is (avoid hallucinating extra details).
      speak = (
        "次の回答文を、日本語で自然に読み上げてください。内容は改変せず、そのまま読み上げます。\n\n"
        + result_text.strip()
      )
      state.aoai_inflight = True
      await rt.create_response(event_id=f"response_grounded_{_now_ms()}", instructions=speak)
      return

    # Grounding failed: mandatory prefix, then answer from general knowledge.
    print(
      "Foundry grounding failed; fallback to general answer",
      {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id},
    )
    fallback_instructions = (
      f"ユーザーの質問に回答してください。冒頭で必ず『{AGENT_FAILURE_PREFIX}』と一言述べてから、一般知識で回答してください。"
    )
    state.aoai_inflight = True
    await rt.create_response(event_id=f"response_fallback_{_now_ms()}", instructions=fallback_instructions)
  finally:
    state.agent_inflight = False


async def _aoai_pump(state: StreamState):
  """Consume AOAI events and trigger response.create so audio is actually generated."""
  rt = state.aoai
  if rt is None:
    return

  async def _barge_in_cancel(*, reason: str, transcript: str | None = None):
    print(
      "AOAI barge-in",
      {
        "callConnectionId": state.call_connection_id,
        "reason": reason,
        "text": transcript,
      },
    )
    # Drop any already-in-flight audio deltas for a short window.
    state.drop_aoai_audio_until_ms = _now_ms() + max(0, int(BARGE_IN_DROP_MS))
    # Clear any buffered audio not yet sent to ACS.
    state.aoai_out_buf.clear()
    state.aoai_to_acs_rate_state = None
    # Best-effort cancel. If unsupported, AOAI will emit an error event.
    try:
      await rt.cancel_response(event_id=f"barge_in_cancel_{_now_ms()}")
    except Exception:
      pass
    state.aoai_inflight = False

  async def _flush_aoai_audio_to_acs():
    if not ACS_SEND_AUDIO:
      return
    if not ACS_SEND_FLUSH_ON_DONE:
      return
    if not state.aoai_out_buf:
      return
    try:
      # Flush any residual samples in the output resampler.
      if state.sample_rate is not None:
        tail, state.aoai_to_acs_rate_state = _resample_pcm16_mono(
          b"",
          src_rate=AOAI_TARGET_RATE,
          dst_rate=int(state.sample_rate),
          state=state.aoai_to_acs_rate_state,
          final=True,
        )
        if tail:
          state.aoai_out_buf.extend(tail)

      payload = bytes(state.aoai_out_buf)
      state.aoai_out_buf.clear()
      b64 = base64.b64encode(payload).decode("ascii")
      await state._acs_ws.send(
        json.dumps(
          {
            "kind": "AudioData",
            "audioData": {
              "data": b64,
            },
          }
        )
      )
    except Exception as e:
      print("ACS flush AudioData failed", {"callConnectionId": state.call_connection_id, "error": repr(e)})

  async def _send_aoai_audio_to_acs(pcm24: bytes):
    # If we just barged-in/cancelled, drop late deltas for a short window.
    if state.drop_aoai_audio_until_ms and _now_ms() < state.drop_aoai_audio_until_ms:
      return

    # Only possible after we received ACS AudioMetadata (so we know target rate).
    if not ACS_SEND_AUDIO:
      return
    if state.sample_rate is None:
      return
    if state.channels not in (None, 1):
      # We only send mono back for now.
      return
    if state.encoding and str(state.encoding).upper() != "PCM":
      return

    # AOAI outputs 24kHz PCM16 mono; resample to ACS input rate (commonly 16kHz).
    pcm_out, state.aoai_to_acs_rate_state = _resample_pcm16_mono(
      pcm24,
      src_rate=AOAI_TARGET_RATE,
      dst_rate=int(state.sample_rate),
      state=state.aoai_to_acs_rate_state,
    )
    if not pcm_out:
      return

    state.aoai_out_buf.extend(pcm_out)
    # Coalesce into bigger frames to reduce overhead.
    if len(state.aoai_out_buf) < ACS_SEND_MIN_CHUNK_BYTES:
      return

    payload = bytes(state.aoai_out_buf)
    state.aoai_out_buf.clear()

    try:
      b64 = base64.b64encode(payload).decode("ascii")
      await state._acs_ws.send(
        json.dumps(
          {
            "kind": "AudioData",
            "audioData": {
              "data": b64,
            },
          }
        )
      )
    except Exception as e:
      print("ACS send AudioData failed", {"callConnectionId": state.call_connection_id, "error": repr(e)})

  async def _fallback_create_response():
    try:
      await asyncio.sleep(AOAI_RESPONSE_FALLBACK_DELAY_MS / 1000.0)
      if AOAI_AUTO_CREATE_RESPONSE and not state.aoai_inflight:
        state.aoai_inflight = True
        await rt.create_response(event_id=f"response_create_{_now_ms()}")
    except asyncio.CancelledError:
      return
    except Exception:
      return

  try:
    async for ev in rt.events():
      t = ev.get("type", "")

      if t in (
        "session.created",
        "session.updated",
        "conversation.created",
        "response.created",
        "response.done",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "input_audio_buffer.committed",
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.failed",
        "error",
      ):
        print("AOAI event", {"type": t, "callConnectionId": state.call_connection_id})

      if t == "response.created":
        state.aoai_inflight = True
        # New response begins; allow audio through.
        state.drop_aoai_audio_until_ms = 0

      # Immediate barge-in: as soon as the user starts speaking, cancel current assistant response.
      if t == "input_audio_buffer.speech_started":
        if BARGE_IN_ON_SPEECH_STARTED and state.aoai_inflight:
          await _barge_in_cancel(reason="speech_started")
          continue

      if t == "response.done":
        state.aoai_inflight = False
        await _flush_aoai_audio_to_acs()
        # If the service didn't emit a dedicated transcript done event, still log what we collected.
        if LOG_AOAI_OUTPUT_TRANSCRIPT and state.aoai_out_transcript_buf:
          text = "".join(state.aoai_out_transcript_buf).strip()
          state.aoai_out_transcript_buf.clear()
          if text:
            print("AOAI output transcript", {"callConnectionId": state.call_connection_id, "text": text})

      if t in ("input_audio_buffer.committed", "input_audio_buffer.speech_stopped"):
        # If transcription is slow/missing, still kick off a response after a short delay.
        if state.aoai_pending_commit_task and not state.aoai_pending_commit_task.done():
          state.aoai_pending_commit_task.cancel()
        state.aoai_pending_commit_task = asyncio.create_task(_fallback_create_response())

      if t == "conversation.item.input_audio_transcription.completed":
        tr = _extract_transcript_text(ev)
        if tr:
          print("AOAI transcription", {"callConnectionId": state.call_connection_id, "text": tr})

        # Barge-in trigger: cancel current response if the user says a stop phrase.
        if tr and _is_barge_in(tr):
          await _barge_in_cancel(reason="phrase", transcript=tr)
          continue

        # Prefer Foundry Agent grounding when enabled.
        if tr and ENABLE_AGENT and not state.aoai_inflight:
          try:
            asyncio.create_task(_agent_answer_or_fallback(state=state, rt=rt, transcript=tr))
          except Exception:
            pass
          continue

        if AOAI_AUTO_CREATE_RESPONSE and not state.aoai_inflight:
          state.aoai_inflight = True
          try:
            await rt.create_response(event_id=f"response_create_{_now_ms()}")
          except Exception:
            state.aoai_inflight = False

      if t in ("conversation.item.input_audio_transcription.failed", "error"):
        print("AOAI error", {"callConnectionId": state.call_connection_id, "event": ev})

      # Assistant output transcript (when available)
      if LOG_AOAI_OUTPUT_TRANSCRIPT and t in (
        "response.audio_transcript.delta",
        "response.audio_transcript.done",
        "response.output_audio_transcript.delta",
        "response.output_audio_transcript.done",
        "response.output_audio_transcription.delta",
        "response.output_audio_transcription.done",
      ):
        if t.endswith(".delta"):
          d = _extract_text_delta(ev)
          if d:
            state.aoai_out_transcript_buf.append(d)
        else:
          full = _extract_transcript_text(ev) or "".join(state.aoai_out_transcript_buf)
          state.aoai_out_transcript_buf.clear()
          full = (full or "").strip()
          if full:
            print("AOAI output transcript", {"callConnectionId": state.call_connection_id, "text": full})

      # Forward AOAI audio deltas back to ACS (bidirectional streaming).
      if t in ("response.output_audio.delta", "response.audio.delta"):
        b64 = ev.get("delta") or ev.get("audio") or ev.get("chunk")
        if not b64:
          continue
        try:
          pcm24 = base64.b64decode(b64)
        except Exception:
          continue
        await _send_aoai_audio_to_acs(pcm24)

      # Some variants emit audio-done separately; flush any remainder.
      if t in ("response.output_audio.done", "response.audio.done"):
        await _flush_aoai_audio_to_acs()

  except asyncio.CancelledError:
    try:
      await _flush_aoai_audio_to_acs()
    except Exception:
      pass
    return
  except Exception as e:
    print(
      "AOAI pump error",
      {"callConnectionId": state.call_connection_id, "correlationId": state.corr_id, "error": repr(e)},
    )


async def handler(ws):
  headers = dict(ws.request.headers)
  state = StreamState(
    call_connection_id=headers.get("x-ms-call-connection-id"),
    corr_id=headers.get("x-ms-call-correlation-id"),
  )
  # Stash the ACS websocket so AOAI pump can send audio back (bidirectional).
  # (We keep this private attribute off the dataclass fields to avoid repr noise.)
  state._acs_ws = ws  # type: ignore[attr-defined]

  print(
    "ACS WS connected (media)",
    {
      "path": ws.request.path,
      "callConnectionId": state.call_connection_id,
      "correlationId": state.corr_id,
      "headers": {
        "sec-websocket-protocol": headers.get("sec-websocket-protocol"),
        "user-agent": headers.get("user-agent"),
        "origin": headers.get("origin"),
      },
    },
  )

  aoai_task: asyncio.Task | None = None

  try:
    async for message in ws:
      if isinstance(message, bytes):
        try:
          text = message.decode("utf-8", errors="strict")
        except Exception:
          print("RX non-utf8 bytes", {"len": len(message), "callConnectionId": state.call_connection_id})
          continue
      else:
        text = message

      obj = _safe_json(text)
      if not isinstance(obj, dict):
        continue

      kind = obj.get("kind")
      if kind == "AudioMetadata":
        md = obj.get("audioMetadata") or {}
        try:
          state.sample_rate = int(md.get("sampleRate")) if md.get("sampleRate") is not None else None
        except Exception:
          state.sample_rate = None
        try:
          state.channels = int(md.get("channels")) if md.get("channels") is not None else None
        except Exception:
          state.channels = None
        state.encoding = md.get("encoding")

        print(
          "AudioMetadata",
          {
            "callConnectionId": state.call_connection_id,
            "correlationId": state.corr_id,
            "encoding": state.encoding,
            "sampleRate": state.sample_rate,
            "channels": state.channels,
            "length": md.get("length"),
          },
        )

        if ENABLE_AOAI and aoai_task is None:
          # Supervisor keeps reconnecting without dropping the call.
          state.aoai_supervisor_task = asyncio.create_task(_aoai_supervisor(state))
          aoai_task = state.aoai_supervisor_task

      elif kind == "AudioData":
        ad = obj.get("audioData") or {}
        b64 = ad.get("data")
        if not b64:
          continue
        try:
          pcm = base64.b64decode(b64)
        except Exception:
          continue

        state.bytes_in += len(pcm)

        if ENABLE_AOAI and state.sample_rate and state.channels in (1, 2):
          # Wait for AOAI connect (best-effort) then forward.
          if aoai_task is None:
            state.aoai_supervisor_task = asyncio.create_task(_aoai_supervisor(state))
            aoai_task = state.aoai_supervisor_task
          # Non-blocking check.
          try:
            await asyncio.wait_for(state.aoai_ready.wait(), timeout=0.0)
          except Exception:
            pass

          rt = state.aoai
          if rt is not None:
            # Pump is owned by supervisor; if absent, it's ok (will start soon).

            pcm_mono = pcm
            if state.channels == 2:
              pcm_mono = _downmix_pcm16_stereo_to_mono(pcm)
              if not pcm_mono:
                # Can't downmix without audioop; skip.
                pcm_mono = b""

            if pcm_mono:
              pcm_out, state.aoai_rate_state = _resample_pcm16_mono(
                pcm_mono,
                src_rate=state.sample_rate,
                dst_rate=AOAI_TARGET_RATE,
                state=state.aoai_rate_state,
              )
              if pcm_out:
                try:
                  await rt.append_audio(pcm_out)
                except Exception:
                  pass

        now = _now_ms()
        if LOG_AUDIO_STATS and now - state.last_stat_ms >= max(200, int(LOG_AUDIO_STATS_INTERVAL_MS)):
          state.last_stat_ms = now
          print(
            "AudioData stats",
            {
              "callConnectionId": state.call_connection_id,
              "bytesIn": state.bytes_in,
            },
          )

      elif kind == "DtmfData":
        dd = obj.get("dtmfData") or {}
        print("DTMF", {"callConnectionId": state.call_connection_id, "data": dd.get("data")})

  except websockets.exceptions.ConnectionClosed as e:
    print(
      "ACS WS closed (media)",
      {
        "callConnectionId": state.call_connection_id,
        "code": e.code,
        "reason": e.reason,
        "bytesIn": state.bytes_in,
      },
    )
  except Exception as e:
    print("ACS WS error (media)", {"callConnectionId": state.call_connection_id, "error": repr(e)})
  finally:
    # Signal shutdown to background tasks.
    try:
      state.closed.set()
    except Exception:
      pass

    if aoai_task is not None:
      try:
        aoai_task.cancel()
      except Exception:
        pass

    if state.aoai_pending_commit_task is not None:
      try:
        state.aoai_pending_commit_task.cancel()
      except Exception:
        pass

    if state.aoai_pump_task is not None:
      try:
        state.aoai_pump_task.cancel()
      except Exception:
        pass

    if state.aoai is not None:
      try:
        await state.aoai.close()
      except Exception:
        pass


async def main():
  _log_audio_config()
  async with websockets.serve(handler, HOST, PORT):
    print(f"ACS media WS server listening on ws://{HOST}:{PORT} (set MEDIA_WS_PORT to change)")
    await asyncio.Future()  # run forever


if __name__ == "__main__":
  asyncio.run(main())
