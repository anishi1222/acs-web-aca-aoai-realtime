from __future__ import annotations

import asyncio
import os
import pathlib
from contextlib import suppress
from typing import Any, Callable

import aiohttp
from aiohttp import web
import httpx
import uvicorn

# Reuse the proven ACS Media Streaming handler logic.
# We'll run it *inside* the gateway by adapting aiohttp's WebSocket to look like
# a websockets-style connection object.
from scripts.acs_media_ws_server import handler as acs_media_ws_handler
from scripts.acs_media_ws_server import _log_audio_config as _log_media_audio_config

PUBLIC_HOST = os.getenv("CALLBACK_URI_HOST", "").rstrip("/")

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8000"))

# Internal FastAPI endpoint. We default to a Unix domain socket to avoid extra TCP ports.
FASTAPI_UDS = (os.getenv("FASTAPI_UDS") or "").strip()
if not FASTAPI_UDS:
  # Keep it inside the repo (and unique per workspace) to avoid permission issues.
  FASTAPI_UDS = str(pathlib.Path(__file__).resolve().parent / ".run" / "fastapi.sock")

# Exposed WebSocket endpoints handled by the gateway.
MEDIA_WS_PATH = os.getenv("GATEWAY_MEDIA_WS_PATH", "/ws/media").strip() or "/ws/media"


class _WSRequest:
  def __init__(self, *, path: str, headers: dict[str, str]):
    self.path = path
    self.headers = headers


class _AiohttpWSAdapter:
  """Adapter to run the existing websockets-based handler on aiohttp."""

  def __init__(self, request: web.Request, ws: web.WebSocketResponse):
    # The upstream handler expects lower-case header keys.
    hdrs = {k.lower(): v for k, v in request.headers.items()}
    self.request = _WSRequest(path=request.path, headers=hdrs)
    self._ws = ws

  def __aiter__(self):
    return self

  async def __anext__(self):
    msg = await self._ws.receive()
    if msg.type == aiohttp.WSMsgType.TEXT:
      return msg.data
    if msg.type == aiohttp.WSMsgType.BINARY:
      return msg.data
    # CLOSE / ERROR
    raise StopAsyncIteration

  async def send(self, data):
    if isinstance(data, (bytes, bytearray, memoryview)):
      await self._ws.send_bytes(bytes(data))
    else:
      await self._ws.send_str(str(data))


async def _proxy_http(request: web.Request) -> web.StreamResponse:
  """Reverse proxy all HTTP requests to the internal FastAPI server."""
  # Always proxy to FastAPI via UDS (single public port design).
  upstream = f"http://fastapi{request.rel_url}"

  # Copy headers but drop hop-by-hop headers.
  hop_by_hop = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
  }
  headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}

  body = await request.read()

  transport = httpx.AsyncHTTPTransport(uds=FASTAPI_UDS)
  async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), transport=transport) as client:
    resp = await client.request(
      request.method,
      upstream,
      headers=headers,
      content=body,
    )

  out = web.Response(status=resp.status_code, body=resp.content)
  for k, v in resp.headers.items():
    if k.lower() in hop_by_hop:
      continue
    # aiohttp forbids setting some headers; ignore failures.
    with suppress(Exception):
      out.headers[k] = v
  return out


async def gateway_handler(request: web.Request) -> web.StreamResponse:
  return await _proxy_http(request)


async def ws_media(request: web.Request) -> web.StreamResponse:
  ws = web.WebSocketResponse(autoping=True, max_msg_size=0)
  await ws.prepare(request)
  adapter = _AiohttpWSAdapter(request, ws)
  # Run the existing handler until the socket closes.
  await acs_media_ws_handler(adapter)
  return ws


ASGIApp = Any


async def start_fastapi(*, fastapi_app: ASGIApp) -> uvicorn.Server:
  # Ensure UDS dir exists and old socket is removed.
  uds_path = pathlib.Path(FASTAPI_UDS)
  uds_path.parent.mkdir(parents=True, exist_ok=True)
  with suppress(FileNotFoundError):
    uds_path.unlink()

  config = uvicorn.Config(
    fastapi_app,
    uds=str(uds_path),
    log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    reload=False,
  )
  server = uvicorn.Server(config)
  asyncio.create_task(server.serve())
  # Wait until started.
  while not server.started:
    await asyncio.sleep(0.05)
  return server


async def start_gateway() -> web.AppRunner:
  app = web.Application()
  app.router.add_get(MEDIA_WS_PATH, ws_media)
  app.router.add_route("*", "/{tail:.*}", gateway_handler)
  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, host=GATEWAY_HOST, port=GATEWAY_PORT)
  await site.start()
  return runner


async def main(*, fastapi_app: ASGIApp):
  # Log audio/resampler configuration on the common entrypoint (gateway).
  # (acs_media_ws_server.py's main() is not executed when used as an imported handler.)
  try:
    _log_media_audio_config()
  except Exception:
    pass

  print(
    "Unified gateway starting",
    {
      "public": PUBLIC_HOST,
      "gateway": f"http://{GATEWAY_HOST}:{GATEWAY_PORT}",
      "fastapi": f"uds://{FASTAPI_UDS}",
      "mediaPath": MEDIA_WS_PATH,
    },
  )

  fastapi_server = None
  gateway_runner = None

  try:
    fastapi_server = await start_fastapi(fastapi_app=fastapi_app)
    try:
      gateway_runner = await start_gateway()
    except OSError as e:
      if getattr(e, "errno", None) == 98:  # EADDRINUSE
        print(
          "ERROR: gateway port already in use",
          {
            "host": GATEWAY_HOST,
            "port": GATEWAY_PORT,
            "hint": "Stop the process using the port, or set GATEWAY_PORT to another value.",
          },
        )
      raise

    await asyncio.Future()
  finally:
    if gateway_runner is not None:
      with suppress(Exception):
        await gateway_runner.cleanup()
    if fastapi_server is not None:
      # Stop uvicorn
      with suppress(Exception):
        fastapi_server.should_exit = True
    # Clean up the UDS file.
    with suppress(Exception):
      pathlib.Path(FASTAPI_UDS).unlink()


def run(*, fastapi_app: ASGIApp) -> None:
  try:
    asyncio.run(main(fastapi_app=fastapi_app))
  except KeyboardInterrupt:
    # Normal shutdown
    return


if __name__ == "__main__":
  # Allow running this module directly for debugging.
  from app import app as _fastapi_app

  run(fastapi_app=_fastapi_app)
