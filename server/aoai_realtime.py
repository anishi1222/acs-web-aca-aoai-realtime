import os, json, base64, asyncio
from pathlib import Path
import websockets
from azure.identity import DefaultAzureCredential

ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")  # https://<resource>.openai.azure.com (or wss://...)
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")  # 例: gpt-realtime
API_KEY = os.getenv("AZURE_OPENAI_API_KEY")  # PoCはキー、推奨はEntra/MI [11](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/supported-languages)
VOICE = os.getenv("AOAI_VOICE", "sage")


_DEFAULT_INSTRUCTIONS = (
  "あなたは株式会社西友（せいゆう）の日本語音声アシスタントです。常に丁寧語（です・ます調）で応答し、なれなれしい言葉遣い・タメ口・過度なフランク表現は避けてください。ユーザーの発話内容に忠実に回答し、根拠のない推測や断定はしません。最新情報が必要な場合は、参照元（URLや資料）を確認して取得できる場合のみ反映し、取得できない場合は『現時点では確認できません』と明確に伝え、必要なURL/情報の提示をお願いしてください。聞き取れない場合は推測せず、日本語で『恐れ入りますが、もう一度お願いいたします。』と聞き返してください。"
)


def _load_instructions() -> str:
  """Load AOAI system instructions.

  Precedence:
  1) AOAI_INSTRUCTIONS_FILE (UTF-8 text)
  2) AOAI_INSTRUCTIONS (inline string)
  3) built-in default
  """
  path = (os.getenv("AOAI_INSTRUCTIONS_FILE") or "").strip()
  if path:
    p = Path(path)
    if not p.is_absolute():
      # Resolve relative paths from the current working directory (typically `server/`).
      p = Path.cwd() / p
    try:
      text = p.read_text(encoding="utf-8")
    except Exception as e:
      raise RuntimeError(f"Failed to read AOAI_INSTRUCTIONS_FILE: {p} ({e})")
    if text.strip():
      return text
    # If the file exists but is empty/whitespace, fall back.

  inline = os.getenv("AOAI_INSTRUCTIONS")
  if inline is not None and inline.strip():
    return inline

  return _DEFAULT_INSTRUCTIONS

def ws_url():
  # Azure OpenAI Realtime WebSocket endpoint [1](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/realtime-audio-websockets)[2](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/realtime-audio-websockets?view=foundry-classic)
  endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
  deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
  if not endpoint or not deployment:
    raise RuntimeError(
      "Missing required env vars: AZURE_OPENAI_ENDPOINT and/or AZURE_OPENAI_DEPLOYMENT"
    )
  endpoint = endpoint.rstrip("/")
  if endpoint.startswith("https://"):
    endpoint = "wss://" + endpoint[len("https://"):]
  elif endpoint.startswith("http://"):
    endpoint = "ws://" + endpoint[len("http://"):]
  return f"{endpoint}/openai/v1/realtime?model={deployment}"

async def auth_headers():
  if API_KEY:
    return {"api-key": API_KEY}
  # Keyless (Entra ID / Managed Identity) は Azure Identity で実装可能 [11](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/supported-languages)
  cred = DefaultAzureCredential()
  token = await asyncio.get_event_loop().run_in_executor(
    None, lambda: cred.get_token("https://cognitiveservices.azure.com/.default")
  )
  return {"Authorization": f"Bearer {token.token}"}

class AOAIRealtime:
  def __init__(self):
    self.ws = None

  async def connect(self):
    headers = await auth_headers()
    self.ws = await websockets.connect(ws_url(), additional_headers=headers)

    instructions = _load_instructions()

    # session.update（イベント仕様）[3](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/realtime-audio-reference?view=foundry-classic)[10](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/realtime-audio-reference)
    await self.ws.send(json.dumps({
      "type": "session.update",
      "event_id": "session_update_1",
      "session": {
        # When required, accepted values are: realtime, transcription, translation.
        "type": "realtime",
        "instructions": instructions,
        # Azure Realtime expects output_modalities + audio input/output settings.
        "output_modalities": ["audio"],
        "audio": {
          "input": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "transcription": {"model": "whisper-1", "language": "ja"},
            "turn_detection": {
              "type": "server_vad",
              "threshold": 0.5,
              "prefix_padding_ms": 300,
              "silence_duration_ms": 1000,
              "create_response": False,
            },
          },
          "output": {
            "voice": VOICE,
            "format": {"type": "audio/pcm", "rate": 24000},
          },
        },
      }
    }))

  async def append_audio(self, pcm16_bytes: bytes):
    # input_audio_buffer.append [3](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/realtime-audio-reference?view=foundry-classic)
    b64 = base64.b64encode(pcm16_bytes).decode("ascii")
    await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64}))

  async def create_response(self, *, event_id: str = "response_create_1", instructions: str | None = None, temperature: float | None = None):
    # response.create (When server_vad is enabled, the server commits audio automatically.)
    payload = {"type": "response.create", "event_id": event_id}
    response = {}
    if instructions:
      response["instructions"] = instructions
    if temperature is not None:
      response["temperature"] = temperature
    if response:
      payload["response"] = response
    await self.ws.send(json.dumps(payload))

  async def cancel_response(self, *, event_id: str = "response_cancel_1"):
    # Best-effort cancel. If unsupported by the service build, it may return an error event.
    await self.ws.send(json.dumps({"type": "response.cancel", "event_id": event_id}))

  async def events(self):
    async for msg in self.ws:
      yield json.loads(msg)

  async def close(self):
    if self.ws:
      await self.ws.close()
