"""
Coordinator — orchestrates all persona explorers for one session.

Upgrades:
  • EventEmitter integration — real-time WebSocket + DB event log
  • Cancellation support — checks session.status == "cancelling" before starting
  • Token usage tracking — stored on session after completion
  • Semaphore-limited concurrency (MAX_CONCURRENT_PERSONAS)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.events import EventEmitter
from agent.explorer import PersonaExplorer
from agent.reporter import generate_report
from ai.client import get_token_usage, reset_token_usage
from config import settings
from db.models import (
    Bug, ConsoleError, NetworkFailure, PageNode, Session,
    SessionStatus, TestRun,
)
from personas.engine import PersonaEngine, PERSONAS

logger = logging.getLogger(__name__)


class SessionCoordinator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_session(self, session: Session) -> None:
        # Abort if already cancelled before we even start
        if session.status == SessionStatus.cancelling:
            session.status = SessionStatus.cancelled
            session.completed_at = datetime.utcnow()
            await self.db.commit()
            return

        emitter = EventEmitter(session.id)
        emitter.set_db(self.db)

        session.status = SessionStatus.running
        session.started_at = datetime.utcnow()
        await self.db.commit()

        reset_token_usage(session.id)
        persona_engine = PersonaEngine()
        started_at = datetime.utcnow()

        await emitter.emit(
            "session_started",
            message=f"QA Copilot starting on {session.url}",
            data={"url": session.url, "personas": [p["name"] for p in PERSONAS]},
        )

        try:
            # Create one TestRun per persona
            test_runs: dict[str, TestRun] = {}
            for persona in PERSONAS:
                tr = TestRun(
                    session_id=session.id,
                    persona_name=persona["name"],
                    persona_style=persona["style"],
                    status="running",
                    started_at=datetime.utcnow(),
                )
                self.db.add(tr)
                test_runs[persona["name"]] = tr
            await self.db.commit()

            # Build explorers
            explorers = [
                PersonaExplorer(
                    session_id=session.id,
                    target_url=session.url,
                    persona=persona,
                    persona_engine=persona_engine,
                    emitter=emitter,
                )
                for persona in PERSONAS[: settings.max_concurrent_personas]
            ]

            # Run with concurrency cap
            sem = asyncio.Semaphore(settings.max_concurrent_personas)

            async def _run(explorer: PersonaExplorer) -> Any:
                async with sem:
                    # Check for cancellation before each persona starts
                    await self.db.refresh(session)
                    if session.status == SessionStatus.cancelling:
                        return None
                    await emitter.emit(
                        "persona_started",
                        message=f"{explorer.persona['name']} starting exploration",
                        persona=explorer.persona["name"],
                    )
                    result = await explorer.run()
                    await emitter.emit(
                        "persona_completed",
                        message=f"{explorer.persona['name']} done — {result.get('pages_visited',0)} pages, {len(result.get('bug_candidates',[]))} bugs",
                        persona=explorer.persona["name"],
                        data={
                            "pages_visited": result.get("pages_visited", 0),
                            "bugs_found": len(result.get("bug_candidates", [])),
                        },
                    )
                    return result

            raw_results = await asyncio.gather(*[_run(e) for e in explorers], return_exceptions=True)

            # Check if cancelled mid-run
            await self.db.refresh(session)
            if session.status == SessionStatus.cancelling:
                session.status = SessionStatus.cancelled
                session.completed_at = datetime.utcnow()
                await self.db.commit()
                await emitter.emit("session_cancelled", message="Session cancelled by user")
                return

            # Persist results
            all_results: list[dict] = []
            for i, result in enumerate(raw_results):
                persona = PERSONAS[i]
                tr = test_runs[persona["name"]]

                if result is None:  # skipped due to cancellation
                    tr.status = "cancelled"
                    await self.db.commit()
                    continue

                if isinstance(result, Exception):
                    logger.error("Persona %s failed: %s", persona["name"], result)
                    tr.status = "failed"
                    await self.db.commit()
                    await emitter.emit(
                        "persona_failed",
                        message=f"{persona['name']} failed: {result}",
                        persona=persona["name"],
                    )
                    continue

                all_results.append(result)
                await self._persist_result(session, tr, result)
                tr.status = "completed"
                tr.completed_at = datetime.utcnow()
                tr.bugs_found = len(result.get("bug_candidates", []))
                tr.pages_visited = result.get("pages_visited", 0)
                await self.db.commit()

            await emitter.emit(
                "progress",
                message="Generating final report…",
                data={"phase": "reporting"},
            )

            # Cross-persona analysis + final report
            cross_report = persona_engine.cross_persona_report()
            completed_at = datetime.utcnow()

            final_report = await generate_report(
                session_id=session.id,
                target_url=session.url,
                all_results=all_results,
                persona_cross_report=cross_report,
                started_at=started_at,
                completed_at=completed_at,
            )

            # Attach token usage
            token_usage = get_token_usage(session.id)
            final_report["token_usage"] = token_usage
            session.total_input_tokens  = token_usage.get("input", 0)
            session.total_output_tokens = token_usage.get("output", 0)

            session.config = final_report
            session.status = SessionStatus.completed
            session.completed_at = completed_at
            await self.db.commit()

            await emitter.emit(
                "session_completed",
                message=(
                    f"Done — {final_report['overall_health'].upper()} · "
                    f"{len(final_report.get('bugs',[]))} bugs · "
                    f"{final_report.get('stats',{}).get('pages_visited',0)} pages"
                ),
                data={
                    "health": final_report["overall_health"],
                    "total_bugs": len(final_report.get("bugs", [])),
                    "pages_visited": final_report.get("stats", {}).get("pages_visited", 0),
                    "token_usage": token_usage,
                },
            )

            logger.info(
                "Session %s completed. Health=%s Bugs=%d Tokens=%s",
                session.id,
                final_report["overall_health"],
                len(final_report.get("bugs", [])),
                token_usage,
            )

        except Exception as exc:
            logger.error("Session %s coordinator error: %s", session.id, exc, exc_info=True)
            session.status = SessionStatus.failed
            session.error_message = str(exc)
            session.completed_at = datetime.utcnow()
            await self.db.commit()
            await emitter.emit(
                "session_failed",
                message=f"Session failed: {exc}",
                data={"error": str(exc)},
            )

    async def _persist_result(
        self, session: Session, test_run: TestRun, result: dict[str, Any]
    ) -> None:
        for node in result.get("page_nodes", []):
            self.db.add(PageNode(
                session_id=session.id,
                url=node["url"],
                title=node.get("title", ""),
                page_type=node.get("page_type", "other"),
                interactive_elements=node.get("interactive_elements", []),
                outgoing_links=node.get("outgoing_links", []),
                load_time_ms=node.get("load_time_ms"),
                has_errors=False,
                visual_quality=node.get("visual_quality"),
            ))

        for err in result.get("console_errors", []):
            self.db.add(ConsoleError(
                session_id=session.id,
                test_run_id=test_run.id,
                error_type=err.error_type,
                message=err.message,
                stack_trace=err.stack_trace,
                url=err.url,
                timestamp=err.timestamp,
            ))

        for fail in result.get("network_failures", []):
            self.db.add(NetworkFailure(
                session_id=session.id,
                test_run_id=test_run.id,
                request_url=fail.request_url,
                method=fail.method,
                status_code=fail.status_code,
                error_text=fail.error_text,
                timestamp=fail.timestamp,
            ))

        for candidate in result.get("bug_candidates", []):
            self.db.add(Bug(
                session_id=session.id,
                test_run_id=test_run.id,
                title=candidate["title"],
                severity=candidate.get("severity", "medium"),
                description=candidate.get("description", ""),
                reproduction_steps=candidate.get("reproduction_steps", []),
                expected_behavior=candidate.get("expected_behavior", ""),
                actual_behavior=candidate.get("actual_behavior", ""),
                screenshot_path=candidate.get("screenshot_path", ""),
                url_at_error=candidate.get("url_at_error", ""),
                element_selector=candidate.get("element_selector"),
                error_type=candidate.get("error_type"),
                persona_name=candidate.get("persona_name"),
                detected_by_vision=candidate.get("detected_by_vision", False),
            ))

        await self.db.commit()
