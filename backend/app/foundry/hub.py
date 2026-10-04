"""Connected Foundry GM clients, so the backend can push messages (e.g. voice rulings) to them."""

import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class FoundryHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    def add(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> int:
        """Send to every connected GM client; returns how many received it."""
        delivered = 0
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception:
                log.warning("Dropping Foundry client that failed to receive a message")
                self._clients.discard(ws)
        return delivered
