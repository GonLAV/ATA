"""
WebSocket connection manager.

Clients connect to /api/sessions/{id}/ws and receive a stream of
JSON-encoded AgentEvent objects as the session runs.

Architecture:
  • One ConnectionManager singleton holds all active WebSocket connections,
    keyed by session_id.
  • The coordinator calls `broadcast(session_id, event)` whenever anything
    noteworthy happens (page visited, bug found, persona done, etc.).
  • Events are also buffered in a per-session deque so late-connecting
    clients (e.g. page refresh during a run) get the last N events immediately
    on connect.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_HISTORY_SIZE = 200  # events buffered per session


class ConnectionManager:
    def __init__(self) -> None:
        # session_id → set of active WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # session_id → recent event buffer (for new connections)
        self._history: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=_HISTORY_SIZE))
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[session_id].add(ws)

        # Replay buffered history to the new client
        history = list(self._history[session_id])
        for event in history:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                break
        logger.debug("WS connected session=%s total=%d", session_id, len(self._connections[session_id]))

    async def disconnect(self, session_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[session_id].discard(ws)
        logger.debug("WS disconnected session=%s", session_id)

    async def broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        """Broadcast an event to all connected clients for this session."""
        self._history[session_id].append(event)
        payload = json.dumps(event)

        dead: list[WebSocket] = []
        for ws in list(self._connections.get(session_id, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(session_id, ws)

    def clear_history(self, session_id: str) -> None:
        self._history.pop(session_id, None)

    async def handle_connection(self, session_id: str, ws: WebSocket) -> None:
        """Full lifecycle handler — call this from the route."""
        await self.connect(session_id, ws)
        try:
            while True:
                # Keep alive — client sends pings, we echo
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect(session_id, ws)


# Singleton
manager = ConnectionManager()
