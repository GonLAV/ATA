from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import (
    Bug, ConsoleError, NetworkFailure, PageNode,
    Session as SessionModel, SessionStatus, TestRun,
)

router = APIRouter(prefix="/sessions", tags=["reports"])


@router.get("/{session_id}/report")
async def get_report(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Full structured report for a completed session."""
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status == SessionStatus.pending:
        raise HTTPException(status_code=202, detail="Session not yet started")
    if session.status == SessionStatus.running:
        raise HTTPException(status_code=202, detail="Session still running")
    if session.status == SessionStatus.failed:
        raise HTTPException(
            status_code=500,
            detail=f"Session failed: {session.error_message}",
        )

    # The full report is stored in session.config by coordinator
    return session.config or {}


@router.get("/{session_id}/bugs")
async def list_bugs(
    session_id: str,
    severity: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List bugs for a session, optionally filtered by severity."""
    q = select(Bug).where(Bug.session_id == session_id)
    if severity:
        q = q.where(Bug.severity == severity)
    q = q.order_by(Bug.created_at.desc())
    result = await db.execute(q)
    bugs = result.scalars().all()

    return [
        {
            "id": b.id,
            "title": b.title,
            "severity": b.severity.value if hasattr(b.severity, "value") else b.severity,
            "description": b.description,
            "reproduction_steps": b.reproduction_steps,
            "expected_behavior": b.expected_behavior,
            "actual_behavior": b.actual_behavior,
            "screenshot_path": b.screenshot_path,
            "url_at_error": b.url_at_error,
            "error_type": b.error_type,
            "persona_name": b.persona_name,
            "created_at": b.created_at.isoformat(),
        }
        for b in bugs
    ]


@router.get("/{session_id}/bugs/{bug_id}/screenshot")
async def get_screenshot(
    session_id: str, bug_id: str, db: AsyncSession = Depends(get_db)
) -> FileResponse:
    result = await db.execute(
        select(Bug).where(Bug.id == bug_id, Bug.session_id == session_id)
    )
    bug = result.scalar_one_or_none()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug not found")
    if not bug.screenshot_path or not os.path.exists(bug.screenshot_path):
        raise HTTPException(status_code=404, detail="Screenshot not available")
    return FileResponse(bug.screenshot_path, media_type="image/png")


@router.get("/{session_id}/test-runs")
async def list_test_runs(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(TestRun).where(TestRun.session_id == session_id)
    )
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "persona_name": r.persona_name,
            "persona_style": r.persona_style,
            "status": r.status,
            "actions_taken": r.actions_taken,
            "pages_visited": r.pages_visited,
            "bugs_found": r.bugs_found,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]


@router.get("/{session_id}/page-map")
async def get_page_map(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Return the discovered navigation graph as nodes + edges."""
    result = await db.execute(
        select(PageNode).where(PageNode.session_id == session_id)
    )
    nodes = result.scalars().all()

    node_list = [
        {
            "url": n.url,
            "title": n.title,
            "page_type": n.page_type,
            "load_time_ms": n.load_time_ms,
            "has_errors": n.has_errors,
        }
        for n in nodes
    ]

    # Build edge list from outgoing_links
    edges = []
    for n in nodes:
        for link in (n.outgoing_links or [])[:10]:
            edges.append({"from": n.url, "to": link})

    return {"nodes": node_list, "edges": edges}


@router.get("/{session_id}/console-errors")
async def list_console_errors(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(ConsoleError).where(ConsoleError.session_id == session_id)
    )
    return [
        {
            "id": e.id,
            "error_type": e.error_type,
            "message": e.message,
            "url": e.url,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in result.scalars().all()
    ]


@router.get("/{session_id}/network-failures")
async def list_network_failures(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(NetworkFailure).where(NetworkFailure.session_id == session_id)
    )
    return [
        {
            "id": f.id,
            "request_url": f.request_url,
            "method": f.method,
            "status_code": f.status_code,
            "error_text": f.error_text,
            "timestamp": f.timestamp.isoformat(),
        }
        for f in result.scalars().all()
    ]
