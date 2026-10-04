"""WebSocket used by the Foundry module (held by the active GM's browser).

Protocol (JSON messages, version 1):

    client → server  {"type": "hello", "v": 1, "token": "..."}           first message, within 10 s
    server → client  {"type": "welcome", "v": 1}
    client → server  {"type": "ruling.request", "id": "...", "request": {RulingRequest}}
    server → client  {"type": "ruling.pending", "id": "...", "origin": "chat" | "voice", ...}
    server → client  {"type": "ruling.result", "id": "...", "origin": ...,
                      "ruling": {...}, "html": "..."}
    server → client  {"type": "ruling.error", "id": "...", "origin": ..., "code": "...",
                      "message": "..."}
    client → server  {"type": "ping"}  →  {"type": "pong"}

Voice-asked rulings arrive as server-initiated ``ruling.pending`` / ``ruling.result`` messages with
``origin: "voice"`` plus ``speaker``, ``query`` and ``mode``.
"""

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.auth import is_valid_token
from app.foundry.messages import run_ruling
from app.rules.models import RulingRequest

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
HELLO_TIMEOUT_SECONDS = 10
POLICY_VIOLATION = 1008

router = APIRouter()


async def _handle_request(ws: WebSocket, message: dict[str, Any]) -> None:
    message_id = str(message.get("id") or "")
    try:
        request = RulingRequest.model_validate(message.get("request") or {})
    except ValidationError as exc:
        await ws.send_json(
            {
                "type": "ruling.error",
                "id": message_id,
                "origin": "chat",
                "code": "bad_request",
                "message": exc.errors(include_url=False)[0]["msg"],
            }
        )
        return
    await ws.send_json({"type": "ruling.pending", "id": message_id, "origin": "chat"})
    state = ws.app.state
    reply = await run_ruling(state.rules_service, state.answer_language, request, message_id)
    await ws.send_json({**reply, "origin": "chat"})


@router.websocket("/ws/foundry")
async def foundry_socket(ws: WebSocket) -> None:
    settings = ws.app.state.settings
    hub = ws.app.state.foundry_hub

    # Browsers don't apply CORS to WebSockets, so check the page origin here.
    origin = ws.headers.get("origin")
    if settings.cors_origins and origin not in settings.cors_origins:
        log.warning("Rejected Foundry socket from origin %r", origin)
        await ws.close(code=POLICY_VIOLATION)
        return

    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=HELLO_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        with contextlib.suppress(Exception):
            await ws.close(code=POLICY_VIOLATION)
        return
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        await ws.close(code=POLICY_VIOLATION)
        return
    if not is_valid_token(hello.get("token"), settings):
        await ws.send_json({"type": "error", "code": "auth.invalid"})
        await ws.close(code=POLICY_VIOLATION)
        return

    hub.add(ws)
    await ws.send_json({"type": "welcome", "v": PROTOCOL_VERSION})
    tasks: set[asyncio.Task] = set()
    try:
        while True:
            message = await ws.receive_json()
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "ruling.request":
                task = asyncio.create_task(_handle_request(ws, message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json({"type": "error", "code": "unknown_type"})
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        hub.remove(ws)
        for task in tasks:
            task.cancel()
