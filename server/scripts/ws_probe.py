#!/usr/bin/env python3

import argparse
import asyncio
import os
import ssl
from urllib.parse import urlparse

import websockets


def _normalize_base_url(url: str) -> str:
    url = (url or "").strip().strip('"').strip("'")
    if not url:
        raise SystemExit("Missing --url (or CALLBACK_URI_HOST env var)")
    # Accept https://... or wss://... and normalize to host.
    if url.startswith("http://") or url.startswith("https://"):
        parsed = urlparse(url)
        if not parsed.hostname:
            raise SystemExit(f"Invalid URL: {url}")
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.hostname}{':' + str(parsed.port) if parsed.port else ''}"

    if url.startswith("ws://") or url.startswith("wss://"):
        parsed = urlparse(url)
        if not parsed.hostname:
            raise SystemExit(f"Invalid URL: {url}")
        return f"{parsed.scheme}://{parsed.hostname}{':' + str(parsed.port) if parsed.port else ''}"

    # Fallback: treat as hostname
    return f"wss://{url}"


async def _probe(uri: str, *, subprotocols: list[str] | None) -> int:
    ssl_ctx = None
    if uri.startswith("wss://"):
        ssl_ctx = ssl.create_default_context()

    try:
        async with websockets.connect(
            uri,
            ssl=ssl_ctx,
            open_timeout=10,
            close_timeout=5,
            subprotocols=subprotocols,
        ) as ws:
            print("connected")
            print("negotiated_subprotocol:", ws.subprotocol)
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
                print("recv:", msg)
            except Exception:
                pass

            await ws.send("ping")
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print("recv:", msg)
            return 0
    except Exception as e:
        print("failed:", type(e).__name__, str(e))
        return 2


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Probe WebSocket reachability for ACS media streaming tunnels.",
    )
    ap.add_argument(
        "--url",
        default=os.getenv("CALLBACK_URI_HOST", ""),
        help="Public base URL (https://... or wss://...). Defaults to CALLBACK_URI_HOST.",
    )
    ap.add_argument(
        "--path",
        default="/ws/media",
        help="WebSocket path to probe (default: /ws/media)",
    )
    ap.add_argument(
        "--subprotocol",
        action="append",
        default=[],
        help="Offer a subprotocol (repeatable).",
    )

    args = ap.parse_args()
    base = _normalize_base_url(args.url)
    uri = f"{base}{args.path}"
    subprotocols = args.subprotocol if args.subprotocol else None

    print("uri:", uri)
    return asyncio.run(_probe(uri, subprotocols=subprotocols))


if __name__ == "__main__":
    raise SystemExit(main())
