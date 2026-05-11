"""
Coordinator — orchestrates all persona explorers for one session.

Flow:
  1. Create a TestRun record per persona
  2. Run all personas concurrently (asyncio.gather)
  3. Collect results, persist bugs + page nodes to DB
  4. Run cross-persona divergence analysis (Persona Cognitive Engine)
  5. Generate + persist final report
  6. Update session status
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.explorer import PersonaExplorer
from agent.reporter import generate_report
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
        session.status = SessionStatus.running
        session.started_at = datetime.utcnow()
        await self.db.commit()

        persona_engine = PersonaEngine()
        started_at = datetime.utcnow()

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

            # Run all personas concurrently
            explorers = [
                PersonaExplorer(
                    session_id=session.id,
                    target_url=session.url,
                    persona=persona,
                    persona_engine=persona_engine,
                )
                for persona in PERSONAS
            ]

            results = await asyncio.gather(
                *[e.run() for e in explorers],
                return_exceptions=True,
            )

            # Persist results
            all_results: list[dict] = []
            for i, result in enumerate(results):
                persona = PERSONAS[i]
                tr = test_runs[persona["name"]]

                if isinstance(result, Exception):
                    logger.error("Persona %s failed: %s", persona["name"], result)
                    tr.status = "failed"
                    await self.db.commit()
                    continue

                all_results.append(result)
                await self._persist_result(session, tr, result)
                tr.status = "completed"
                tr.completed_at = datetime.utcnow()
                tr.bugs_found = len(result.get("bug_candidates", []))
                tr.pages_visited = result.get("pages_visited", 0)
                await self.db.commit()

            # Cross-persona analysis
            cross_report = persona_engine.cross_persona_report()
            completed_at = datetime.utcnow()

            # Final AI report
            final_report = await generate_report(
                session_id=session.id,
                target_url=session.url,
                all_results=all_results,
                persona_cross_report=cross_report,
                started_at=started_at,
                completed_at=completed_at,
            )

            # Store report as session config (reusing JSON field)
            session.config = final_report
            session.status = SessionStatus.completed
            session.completed_at = completed_at
            await self.db.commit()

            logger.info(
                "Session %s completed. Health: %s. Bugs: %d",
                session.id,
                final_report["overall_health"],
                len(final_report["bugs"]),
            )

        except Exception as exc:
            logger.error("Session %s coordinator error: %s", session.id, exc, exc_info=True)
            session.status = SessionStatus.failed
            session.error_message = str(exc)
            session.completed_at = datetime.utcnow()
            await self.db.commit()

    async def _persist_result(
        self, session: Session, test_run: TestRun, result: dict[str, Any]
    ) -> None:
        # Page nodes
        for node in result.get("page_nodes", []):
            self.db.add(
                PageNode(
                    session_id=session.id,
                    url=node["url"],
                    title=node.get("title", ""),
                    page_type=node.get("page_type", "other"),
                    interactive_elements=node.get("interactive_elements", []),
                    outgoing_links=node.get("outgoing_links", []),
                    load_time_ms=node.get("load_time_ms"),
                    has_errors=False,
                )
            )

        # Console errors
        for err in result.get("console_errors", []):
            self.db.add(
                ConsoleError(
                    session_id=session.id,
                    test_run_id=test_run.id,
                    error_type=err.error_type,
                    message=err.message,
                    stack_trace=err.stack_trace,
                    url=err.url,
                    timestamp=err.timestamp,
                )
            )

        # Network failures
        for fail in result.get("network_failures", []):
            self.db.add(
                NetworkFailure(
                    session_id=session.id,
                    test_run_id=test_run.id,
                    request_url=fail.request_url,
                    method=fail.method,
                    status_code=fail.status_code,
                    error_text=fail.error_text,
                    timestamp=fail.timestamp,
                )
            )

        # Bugs
        for candidate in result.get("bug_candidates", []):
            self.db.add(
                Bug(
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
                )
            )

        await self.db.commit()
