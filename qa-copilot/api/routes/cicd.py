"""CI/CD export endpoints — JUnit, SARIF, badge, Markdown summary."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Session as SessionModel, SessionStatus
from cicd.exporter import to_junit_xml, to_badge_json, to_sarif, to_github_summary, ci_exit_code

router = APIRouter(prefix="/sessions", tags=["ci-cd"])


async def _get_completed_report(session_id: str, db: AsyncSession) -> dict:
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.completed:
        raise HTTPException(status_code=202, detail=f"Session status: {session.status.value}")
    return session.config or {}


@router.get("/{session_id}/export/junit", response_class=PlainTextResponse)
async def export_junit(session_id: str, db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    report = await _get_completed_report(session_id, db)
    return PlainTextResponse(to_junit_xml(report), media_type="application/xml")


@router.get("/{session_id}/export/sarif")
async def export_sarif(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    report = await _get_completed_report(session_id, db)
    return to_sarif(report)


@router.get("/{session_id}/export/badge")
async def export_badge(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    report = await _get_completed_report(session_id, db)
    return to_badge_json(report)


@router.get("/{session_id}/export/summary", response_class=PlainTextResponse)
async def export_summary(session_id: str, db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    report = await _get_completed_report(session_id, db)
    return PlainTextResponse(to_github_summary(report), media_type="text/markdown")


@router.get("/{session_id}/export/gate")
async def ci_gate(session_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Returns {pass: bool, exit_code: int, health: str} for CI gate decisions."""
    report = await _get_completed_report(session_id, db)
    code = ci_exit_code(report)
    return JSONResponse({
        "pass": code == 0,
        "exit_code": code,
        "health": report.get("overall_health", "unknown"),
        "critical_bugs": sum(1 for b in report.get("bugs", []) if b.get("severity") == "critical"),
        "high_bugs": sum(1 for b in report.get("bugs", []) if b.get("severity") == "high"),
    })
