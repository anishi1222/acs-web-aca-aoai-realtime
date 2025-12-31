import asyncio
import os
import time
from dataclasses import dataclass

from azure.identity.aio import DefaultAzureCredential

try:
  from azure.ai.projects.aio import AIProjectClient  # type: ignore
except Exception:  # pragma: no cover
  AIProjectClient = None  # type: ignore


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


def _env_int(name: str, default: int) -> int:
  raw = (os.getenv(name) or "").strip()
  if not raw:
    return default
  try:
    return int(raw)
  except Exception:
    return default


def _env_str(name: str, default: str | None = None) -> str | None:
  v = os.getenv(name)
  if v is None:
    return default
  s = v.strip()
  return s if s else default


@dataclass
class AgentConfig:
  enabled: bool
  project_endpoint: str | None
  agent_id: str | None
  timeout_ms: int
  max_output_chars: int

  @staticmethod
  def from_env() -> "AgentConfig":
    project_endpoint = _env_str("AZURE_AI_PROJECT_ENDPOINT") or _env_str("AZURE_FOUNDRY_PROJECT_ENDPOINT")
    agent_id = _env_str("AZURE_AI_AGENT_ID") or _env_str("AZURE_FOUNDRY_AGENT_ID")

    enabled_default = bool(project_endpoint and agent_id)
    enabled = _env_bool("MEDIA_WS_AGENT_ENABLE", enabled_default)

    return AgentConfig(
      enabled=enabled,
      project_endpoint=project_endpoint,
      agent_id=agent_id,
      timeout_ms=_env_int("MEDIA_WS_AGENT_TIMEOUT_MS", 2000),
      max_output_chars=_env_int("MEDIA_WS_AGENT_MAX_OUTPUT_CHARS", 1200),
    )


class FoundryWebGroundingAgent:
  """Thin wrapper to call an existing Foundry Agent configured with Web grounding.

  Expected env vars:
  - AZURE_AI_PROJECT_ENDPOINT (or AZURE_FOUNDRY_PROJECT_ENDPOINT)
  - AZURE_AI_AGENT_ID (or AZURE_FOUNDRY_AGENT_ID)

  Optional:
  - MEDIA_WS_AGENT_ENABLE
  - MEDIA_WS_AGENT_TIMEOUT_MS
  - MEDIA_WS_AGENT_MAX_OUTPUT_CHARS
  """

  def __init__(self, config: AgentConfig | None = None):
    self.config = config or AgentConfig.from_env()

  async def run(self, *, query: str, correlation: dict | None = None) -> str | None:
    cfg = self.config
    if not cfg.enabled:
      return None
    if not cfg.project_endpoint or not cfg.agent_id:
      return None
    if AIProjectClient is None:
      return None

    q = (query or "").strip()
    if not q:
      return None

    start = time.time()

    async def _do_run() -> str | None:
      async with DefaultAzureCredential() as cred:
        async with AIProjectClient(endpoint=cfg.project_endpoint, credential=cred) as project:
          thread = await project.agents.threads.create()
          await project.agents.messages.create(thread_id=thread.id, role="user", content=q)
          run = await project.agents.runs.create_and_process(thread_id=thread.id, agent_id=cfg.agent_id)
          if getattr(run, "status", None) == "failed":
            return None

          # Fetch messages and return the most recent assistant text.
          messages = await project.agents.messages.list(thread_id=thread.id)
          # SDK shapes vary; try common patterns.
          # 1) iterable of message objects
          last_text: str | None = None
          try:
            for m in messages:
              role = getattr(m, "role", None)
              if role != "assistant":
                continue
              # Prefer text_messages[-1].text.value (per docs)
              text_messages = getattr(m, "text_messages", None)
              if text_messages:
                try:
                  last = text_messages[-1]
                  text_obj = getattr(last, "text", None)
                  value = getattr(text_obj, "value", None)
                  if isinstance(value, str) and value.strip():
                    last_text = value
                except Exception:
                  pass
              # Fallback: content string
              content = getattr(m, "content", None)
              if isinstance(content, str) and content.strip():
                last_text = content
          except Exception:
            last_text = None

          if last_text is None:
            return None

          out = last_text.strip()
          if not out:
            return None
          if cfg.max_output_chars > 0 and len(out) > cfg.max_output_chars:
            out = out[: cfg.max_output_chars].rstrip() + "…"
          return out

    try:
      return await asyncio.wait_for(_do_run(), timeout=cfg.timeout_ms / 1000.0)
    except asyncio.TimeoutError:
      return None
    except Exception:
      return None
    finally:
      elapsed_ms = int((time.time() - start) * 1000)
      if correlation is None:
        correlation = {}
      # Keep logging in the caller; this module stays quiet by default.
      _ = elapsed_ms
