"""
Agent event system.

All significant agent actions are represented as typed events and:
  1. Broadcast to connected WebSocket clients (live feed).
  2. Persisted to the SessionEvent table (survives page refresh).

Event types:
  session_started      session_completed    session_failed
  persona_started      persona_completed    persona_failed
  page_visited         page_analysis_done
  scenario_started     scenario_completed
  bug_found            bug_skipped
  vision_insight       accessibility_issue
  progress             log
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    type: str
    session_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    persona: str | None = None
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EventEmitter:
    """
    Emits events to WebSocket clients and optionally persists them.
    One instance per coordinator run.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._db_session: Any = None  # set after init if persistence is wanted

    def set_db(self, db: Any) -> None:
        self._db_session = db

    async def emit(
        self,
        event_type: str,
        message: str = "",
        persona: str | None = None,
        data: dict | None = None,
    ) -> None:
        from api.websocket import manager

        event = AgentEvent(
            type=event_type,
            session_id=self.session_id,
            persona=persona,
            message=message,
            data=data or {},
        )
        await manager.broadcast(self.session_id, event.to_dict())

        # Persist to DB
        if self._db_session is not None:
            try:
                from db.models import SessionEvent
                self._db_session.add(
                    SessionEvent(
                        session_id=self.session_id,
                        event_type=event_type,
                        persona=persona,
                        message=message,
                        data=data or {},
                    )
                )
                await self._db_session.commit()
            except Exception as exc:
                logger.debug("Event persist failed: %s", exc)

    # Convenience helpers
    async def log(self, msg: str, persona: str | None = None) -> None:
        await self.emit("log", msg, persona=persona)

    async def page_visited(self, url: str, title: str, page_type: str, persona: str, load_ms: float) -> None:
        await self.emit(
            "page_visited",
            message=f"Visited: {url}",
            persona=persona,
            data={"url": url, "title": title, "page_type": page_type, "load_ms": load_ms},
        )

    async def bug_found(self, title: str, severity: str, persona: str, url: str) -> None:
        await self.emit(
            "bug_found",
            message=f"[{severity.upper()}] {title}",
            persona=persona,
            data={"title": title, "severity": severity, "url": url},
        )

    async def progress(self, persona: str, pages: int, bugs: int, depth: int) -> None:
        await self.emit(
            "progress",
            message=f"{persona}: {pages} pages, {bugs} bugs",
            persona=persona,
            data={"pages": pages, "bugs": bugs, "depth": depth},
        )

    async def vision_insight(self, persona: str, url: str, issues: list[str]) -> None:
        if issues:
            await self.emit(
                "vision_insight",
                message=f"Vision: {issues[0]}" if issues else "Visual analysis complete",
                persona=persona,
                data={"url": url, "issues": issues},
            )
