
import os, time, asyncio, json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from azure.communication.identity import CommunicationIdentityClient
from azure.core.exceptions import ClientAuthenticationError
from azure.communication.callautomation import (
    CallAutomationClient,
    ServerCallLocator,
    MediaStreamingOptions,
    StreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
  CommunicationUserIdentifier,
)

try:
  from azure.communication.callautomation import AudioFormat  # type: ignore
except Exception:
  AudioFormat = None  # type: ignore

app = FastAPI()


# --- Env / config ---
ACS_CONN = os.getenv("AZURE_COMMUNICATION_CONNECTION_STRING")
CALLBACK_URI_HOST = os.getenv("CALLBACK_URI_HOST")  # e.g. "https://my-server.com"
AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AOAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
AOAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AOAI_VOICE = os.getenv("AOAI_VOICE")


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


def _env_str(name: str, default: str) -> str:
  v = os.getenv(name)
  if v is None:
    return default
  s = v.strip()
  return s if s else default


def _select_acs_audio_format():
  """Select ACS media streaming audio format.

  Supported by ACS: PCM 16k mono, PCM 24k mono.
  Use env `ACS_MEDIA_AUDIO_FORMAT` with values like: pcm16k, pcm24k.
  """
  if AudioFormat is None:
    return None
  raw = _env_str("ACS_MEDIA_AUDIO_FORMAT", "pcm16k").lower()
  if raw in ("pcm16", "pcm16k", "pcm16_k", "16k", "16khz"):
    return AudioFormat.PCM16_K_MONO
  if raw in ("pcm24", "pcm24k", "pcm24_k", "24k", "24khz"):
    return AudioFormat.PCM24_K_MONO
  # Default safe choice.
  return AudioFormat.PCM16_K_MONO


def _select_acs_audio_channel_type() -> MediaStreamingAudioChannelType:
  raw = _env_str("ACS_MEDIA_AUDIO_CHANNEL_TYPE", "mixed").lower()
  if raw in ("mixed", "mix"):
    return MediaStreamingAudioChannelType.MIXED
  if raw in ("unmixed", "unmix"):
    return MediaStreamingAudioChannelType.UNMIXED
  return MediaStreamingAudioChannelType.MIXED


def _mask_secret(value: str | None, *, show_last: int = 4) -> str | None:
  if value is None:
    return None
  v = value.strip()
  if not v:
    return ""
  if len(v) <= show_last:
    return "*" * len(v)
  return "*" * (len(v) - show_last) + v[-show_last:]


def _expires_on_to_string(expires_on) -> str | None:
  """Normalize SDK return types to a JSON-friendly string.

  Depending on azure-communication-identity SDK versions, `expires_on` may be:
  - a datetime-like object (has .isoformat())
  - an ISO-8601 string
  """
  if expires_on is None:
    return None
  if isinstance(expires_on, str):
    return expires_on
  iso = getattr(expires_on, "isoformat", None)
  if callable(iso):
    try:
      return iso()
    except Exception:
      pass
  return str(expires_on)


def _acs_conn_string_parts(conn: str | None) -> dict:
  if not conn:
    return {}
  parts: dict[str, str] = {}
  for seg in conn.split(";"):
    seg = seg.strip()
    if not seg or "=" not in seg:
      continue
    k, v = seg.split("=", 1)
    parts[k.strip().lower()] = v.strip()
  return parts


def _acs_conn_string_sanity(conn: str | None) -> dict:
  parts = _acs_conn_string_parts(conn)
  endpoint = parts.get("endpoint")
  accesskey = parts.get("accesskey")
  return {
    "present": bool(conn and conn.strip()),
    "hasEndpoint": bool(endpoint),
    "hasAccessKey": bool(accesskey),
    "endpoint": endpoint,
    "accessKeyMasked": _mask_secret(accesskey),
  }


call_automation_client = None
if ACS_CONN:
  call_automation_client = CallAutomationClient.from_connection_string(ACS_CONN)


def _require_callback_uri_host() -> str:
  host = (CALLBACK_URI_HOST or "").strip()
  if not host:
    raise RuntimeError("CALLBACK_URI_HOST not set")
  return host.rstrip("/")


def _ws_transport_url() -> str:
  # ACS Media Streaming requires ws(s)://. We derive it from CALLBACK_URI_HOST.
  host = _require_callback_uri_host()
  if host.startswith("https://"):
    ws_host = "wss://" + host[len("https://"):]
  elif host.startswith("http://"):
    ws_host = "ws://" + host[len("http://"):]
  else:
    ws_host = host
  return f"{ws_host}/ws/media"


def _media_streaming_options() -> MediaStreamingOptions:
  # Keep this simple: start streaming immediately; bidirectional is optional.
  enable_bidi = _env_bool("ACS_MEDIA_ENABLE_BIDIRECTIONAL", True)
  kwargs: dict = {
    "start_media_streaming": True,
    "enable_bidirectional": enable_bidi,
  }
  if AudioFormat is not None:
    fmt = _select_acs_audio_format()
    if fmt is not None:
      kwargs["audio_format"] = fmt

  return MediaStreamingOptions(
    transport_url=_ws_transport_url(),
    transport_type=StreamingTransportType.WEBSOCKET,
    content_type=MediaStreamingContentType.AUDIO,
    audio_channel_type=_select_acs_audio_channel_type(),
    **kwargs,
  )


def _parse_acs_events(body: bytes) -> list[dict]:
  """Parse ACS Call Automation webhook payloads.

  The Python SDK no longer exposes CallAutomationEventParser in some versions.
  This keeps us compatible by parsing common JSON shapes:
  - Event Grid style: a JSON array of event objects
  - CloudEvents style: a single JSON object, sometimes wrapped in {"value": [...]}.
  """
  try:
    payload = json.loads(body.decode("utf-8"))
  except Exception:
    return []

  if isinstance(payload, list):
    events = payload
  elif isinstance(payload, dict) and isinstance(payload.get("value"), list):
    events = payload["value"]
  elif isinstance(payload, dict):
    events = [payload]
  else:
    return []

  normalized: list[dict] = []
  for ev in events:
    if not isinstance(ev, dict):
      continue
    ev_type = ev.get("type") or ev.get("eventType")
    data = ev.get("data") or {}
    normalized.append({"type": ev_type, "data": data, "raw": ev})
  return normalized

@app.post("/api/incomingCall")
async def incoming_call_handler(request: Request):
    if not call_automation_client:
        return JSONResponse({"error": "ACS not configured"}, status_code=500)
    
    # Parse the incoming event
    events = _parse_acs_events(await request.body())
    for event in events:
      if event.get("type") == "Microsoft.Communication.IncomingCall":
        incoming_call_context = (event.get("data") or {}).get("incomingCallContext")
        if not incoming_call_context:
          continue

        try:
          media_streaming_options = _media_streaming_options()
          callback_url = f"{_require_callback_uri_host()}/api/callbacks"

          await asyncio.to_thread(
            call_automation_client.answer_call,
            incoming_call_context=incoming_call_context,
            callback_url=callback_url,
            media_streaming=media_streaming_options,
          )
          print(f"Answered call with media streaming to {_ws_transport_url()}")
        except Exception as e:
          print(f"Failed to answer call: {e}")
          return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

    return JSONResponse({"status": "ok"})

@app.post("/api/callbacks")
async def call_automation_callback(request: Request):
    # Handle other call automation events if needed (e.g. CallConnected, CallDisconnected)
  events = _parse_acs_events(await request.body())
  for event in events:
    ev_type = event.get("type")
    print(f"Received ACS event: {ev_type}")

    # Media streaming failures often include useful diagnostics under `data`.
    if ev_type in (
      "Microsoft.Communication.MediaStreamingFailed",
      "Microsoft.Communication.MediaStreamingStarted",
      "Microsoft.Communication.MediaStreamingStopped",
    ):
      try:
        print("ACS media streaming event data:", json.dumps(event.get("data") or {}, ensure_ascii=False))
      except Exception:
        print("ACS media streaming event data:", event.get("data"))
  return JSONResponse({"status": "ok"})


@app.get("/api/health")
def health():
  """Lightweight config check for local debugging.

  Intentionally does NOT return secrets.
  """
  callback_host = (CALLBACK_URI_HOST or "").strip()
  callback_ok = bool(callback_host)
  ws_url = None
  if callback_ok:
    try:
      ws_url = _ws_transport_url()
    except Exception:
      ws_url = None

  acs_info = _acs_conn_string_sanity(ACS_CONN)
  return JSONResponse(
    {
      "ok": True,
      "acs": {
        "callAutomationClientConfigured": call_automation_client is not None,
        "identityClientConfigured": ACS_CONN is not None and bool(ACS_CONN.strip()),
        **acs_info,
      },
      "aoai": {
        "endpointSet": bool(AOAI_ENDPOINT and AOAI_ENDPOINT.strip()),
        "deploymentSet": bool(AOAI_DEPLOYMENT and AOAI_DEPLOYMENT.strip()),
        "apiKeySet": bool(AOAI_API_KEY and AOAI_API_KEY.strip()),
        "voiceSet": bool(AOAI_VOICE and AOAI_VOICE.strip()),
      },
      "callback": {
        "callbackUriHostSet": callback_ok,
        "callbackUriHost": callback_host.rstrip("/") if callback_ok else None,
        "mediaStreamingTransportUrl": ws_url,
      },
      "mediaStreaming": {
        "enableBidirectional": _env_bool("ACS_MEDIA_ENABLE_BIDIRECTIONAL", True),
        "audioFormat": _env_str("ACS_MEDIA_AUDIO_FORMAT", "pcm16k"),
        "audioChannelType": _env_str("ACS_MEDIA_AUDIO_CHANNEL_TYPE", "mixed"),
      },
    }
  )


class StartServerCallRequest(BaseModel):
  targetUserId: str
  sourceDisplayName: str | None = None


@app.post("/api/call/start")
async def start_server_call(payload: StartServerCallRequest):
  """Server-initiated outbound call.

  Local/on-prem testing tip:
  - This avoids the need for Event Grid routing to /api/incomingCall.
  - But ACS still needs to reach this server for callbacks and WS media streaming,
    so CALLBACK_URI_HOST must be a publicly reachable https:// URL.
  """
  if not call_automation_client:
    return JSONResponse({"error": "ACS not configured"}, status_code=500)

  target_user_id = (payload.targetUserId or "").strip()
  if not target_user_id:
    return JSONResponse({"error": "targetUserId is required"}, status_code=400)

  try:
    callback_host = _require_callback_uri_host()
    media_streaming_options = _media_streaming_options()
  except Exception as e:
    return JSONResponse(
      {
        "error": str(e),
        "hint": "ローカル実行時は ngrok / cloudflared 等で https:// の公開URLを作り、CALLBACK_URI_HOST に設定してください",
      },
      status_code=500,
    )

  callback_url = f"{callback_host}/api/callbacks"
  target = CommunicationUserIdentifier(target_user_id)
  source_display_name = payload.sourceDisplayName or "Realtime Server"

  print(
    "create_call:",
    {
      "targetUserId": target_user_id,
      "callbackUrl": callback_url,
      "mediaStreamingTransportUrl": _ws_transport_url(),
      "mediaStreaming": {
        "enableBidirectional": _env_bool("ACS_MEDIA_ENABLE_BIDIRECTIONAL", True),
        "audioFormat": _env_str("ACS_MEDIA_AUDIO_FORMAT", "pcm16k"),
        "audioChannelType": _env_str("ACS_MEDIA_AUDIO_CHANNEL_TYPE", "mixed"),
      },
    },
  )

  try:
    result = await asyncio.to_thread(
      call_automation_client.create_call,
      target_participant=target,
      callback_url=callback_url,
      source_display_name=source_display_name,
      media_streaming=media_streaming_options,
    )
  except Exception as e:
    return JSONResponse({"error": f"create_call failed: {e}"}, status_code=500)

  call_connection_id = getattr(result, "call_connection_id", None) or getattr(result, "callConnectionId", None)
  server_call_id = getattr(result, "server_call_id", None) or getattr(result, "serverCallId", None)
  print(
    "create_call result:",
    {"callConnectionId": call_connection_id, "serverCallId": server_call_id},
  )
  return JSONResponse(
    {
      "ok": True,
      "callConnectionId": call_connection_id,
      "serverCallId": server_call_id,
      "callbackUrl": callback_url,
      "mediaStreamingTransportUrl": _ws_transport_url(),
    }
  )


# --- ACS token endpoint（Calling SDK 用） ---
identity_client = None
if ACS_CONN:
  identity_client = CommunicationIdentityClient.from_connection_string(ACS_CONN)

@app.get("/api/token")
def token():
  if not identity_client:
    return JSONResponse(
      {"error": "AZURE_COMMUNICATION_CONNECTION_STRING が未設定のため /api/token は利用できません"},
      status_code=500,
    )

  try:
    user = identity_client.create_user()
    tok = identity_client.get_token(user, scopes=["voip"])
  except ClientAuthenticationError as e:
    # Common causes:
    # - Connection string is wrong (wrong resource / rotated key)
    # - The ACS resource is deleted or not accessible
    # - Env var is pointing at a different subscription/tenant in CI
    sanity = _acs_conn_string_sanity(ACS_CONN)
    return JSONResponse(
      {
        "error": "ACS 認証に失敗しました (Denied)",
        "details": str(e),
        "acsConnectionString": sanity,
        "hint": (
          "AZURE_COMMUNICATION_CONNECTION_STRING が正しい ACS リソースの接続文字列か確認してください。"
          "（キーをローテーションした場合は新しい接続文字列に更新）"
        ),
        "next": "GET /api/health で設定状況を確認できます",
      },
      status_code=401,
    )
  except Exception as e:
    return JSONResponse(
      {"error": "ACS トークン発行に失敗しました", "details": str(e)},
      status_code=500,
    )

  return JSONResponse(
    {
      "userId": user.properties["id"],
      "token": tok.token,
      "expiresOn": _expires_on_to_string(getattr(tok, "expires_on", None)),
    }
  )


def _run_as_main() -> None:
  # Start unified gateway (public :8000, FastAPI on UDS, media WS handled by gateway)
  from unified_gateway import run as run_gateway

  run_gateway(fastapi_app=app)


if __name__ == "__main__":
  _run_as_main()
