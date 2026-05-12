"""Analytics — usage dashboards and insights (n8n: usage dashboards + insights)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Bug, Session, SessionStatus, Severity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """High-level totals for the dashboard summary cards."""
    total_sessions = await db.scalar(select(func.count(Session.id)))
    completed      = await db.scalar(select(func.count(Session.id)).where(Session.status == SessionStatus.completed))
    failed         = await db.scalar(select(func.count(Session.id)).where(Session.status == SessionStatus.failed))
    total_bugs     = await db.scalar(select(func.count(Bug.id)))
    critical_bugs  = await db.scalar(select(func.count(Bug.id)).where(Bug.severity == Severity.critical))
    high_bugs      = await db.scalar(select(func.count(Bug.id)).where(Bug.severity == Severity.high))

    # Average session duration (seconds) for completed sessions
    result = await db.execute(
        select(Session.started_at, Session.completed_at)
        .where(Session.status == SessionStatus.completed)
        .where(Session.started_at.isnot(None))
        .where(Session.completed_at.isnot(None))
        .limit(200)
    )
    rows = result.all()
    avg_duration = 0.0
    if rows:
        durations = [(r.completed_at - r.started_at).total_seconds() for r in rows]
        avg_duration = sum(durations) / len(durations)

    # Token usage totals
    tok_result = await db.execute(
        select(
            func.sum(Session.total_input_tokens),
            func.sum(Session.total_output_tokens),
        )
    )
    tok_row = tok_result.first()
    total_input_tokens  = tok_row[0] or 0
    total_output_tokens = tok_row[1] or 0
    total_cost_usd = (total_input_tokens / 1_000_000 * 3.0) + (total_output_tokens / 1_000_000 * 15.0)

    return {
        "total_sessions":       total_sessions or 0,
        "completed_sessions":   completed or 0,
        "failed_sessions":      failed or 0,
        "total_bugs":           total_bugs or 0,
        "critical_bugs":        critical_bugs or 0,
        "high_bugs":            high_bugs or 0,
        "avg_duration_seconds": round(avg_duration, 1),
        "total_input_tokens":   total_input_tokens,
        "total_output_tokens":  total_output_tokens,
        "total_cost_usd":       round(total_cost_usd, 4),
    }


@router.get("/sessions-over-time")
async def sessions_over_time(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Daily session counts for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(Session.created_at, Session.status)
        .where(Session.created_at >= since)
        .order_by(Session.created_at)
    )
    rows = result.all()

    buckets: dict[str, dict] = {}
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d")
        if day not in buckets:
            buckets[day] = {"date": day, "total": 0, "completed": 0, "failed": 0}
        buckets[day]["total"] += 1
        if row.status == SessionStatus.completed:
            buckets[day]["completed"] += 1
        elif row.status == SessionStatus.failed:
            buckets[day]["failed"] += 1

    return list(buckets.values())


@router.get("/bugs-over-time")
async def bugs_over_time(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Daily bug counts grouped by severity for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(Bug.created_at, Bug.severity)
        .where(Bug.created_at >= since)
        .order_by(Bug.created_at)
    )
    rows = result.all()

    buckets: dict[str, dict] = {}
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d")
        if day not in buckets:
            buckets[day] = {"date": day, "critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        sev = row.severity.value if hasattr(row.severity, "value") else str(row.severity)
        buckets[day][sev] = buckets[day].get(sev, 0) + 1
        buckets[day]["total"] += 1

    return list(buckets.values())


@router.get("/bugs-by-severity")
async def bugs_by_severity(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    """Total bug counts grouped by severity (all time)."""
    result = await db.execute(
        select(Bug.severity, func.count(Bug.id)).group_by(Bug.severity)
    )
    return {
        (row[0].value if hasattr(row[0], "value") else str(row[0])): row[1]
        for row in result.all()
    }


@router.get("/top-urls")
async def top_urls(
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Most-tested URLs with session counts and average bug rates."""
    result = await db.execute(
        select(Session.url, func.count(Session.id).label("sessions"))
        .group_by(Session.url)
        .order_by(func.count(Session.id).desc())
        .limit(limit)
    )
    rows = result.all()
    out = []
    for row in rows:
        bug_count = await db.scalar(
            select(func.count(Bug.id))
            .join(Session, Bug.session_id == Session.id)
            .where(Session.url == row.url)
        )
        out.append({"url": row.url, "sessions": row.sessions, "total_bugs": bug_count or 0})
    return out


@router.get("/token-usage")
async def token_usage(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Daily token consumption over the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(Session.created_at, Session.total_input_tokens, Session.total_output_tokens)
        .where(Session.created_at >= since)
        .where(Session.total_input_tokens > 0)
        .order_by(Session.created_at)
    )
    rows = result.all()
    buckets: dict[str, dict] = {}
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d")
        if day not in buckets:
            buckets[day] = {"date": day, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        inp = row.total_input_tokens or 0
        out = row.total_output_tokens or 0
        buckets[day]["input_tokens"]  += inp
        buckets[day]["output_tokens"] += out
        buckets[day]["cost_usd"]      += (inp / 1_000_000 * 3.0) + (out / 1_000_000 * 15.0)

    for v in buckets.values():
        v["cost_usd"] = round(v["cost_usd"], 4)
    return list(buckets.values())


@router.get("/search")
async def search(
    q: str = Query(..., min_length=2),
    kind: str = Query(default="all"),   # all | sessions | bugs
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full-text search across sessions (by URL) and bugs (by title/description)."""
    pattern = f"%{q}%"
    results: dict[str, Any] = {}

    if kind in ("all", "sessions"):
        sess_result = await db.execute(
            select(Session)
            .where(Session.url.ilike(pattern))
            .order_by(Session.created_at.desc())
            .limit(limit)
        )
        sessions = sess_result.scalars().all()
        results["sessions"] = [
            {
                "id": s.id,
                "url": s.url,
                "status": s.status.value,
                "created_at": s.created_at.isoformat(),
            }
            for s in sessions
        ]

    if kind in ("all", "bugs"):
        bug_result = await db.execute(
            select(Bug)
            .where(Bug.title.ilike(pattern) | Bug.description.ilike(pattern))
            .order_by(Bug.created_at.desc())
            .limit(limit)
        )
        bugs = bug_result.scalars().all()
        results["bugs"] = [
            {
                "id": b.id,
                "title": b.title,
                "severity": b.severity.value if hasattr(b.severity, "value") else b.severity,
                "session_id": b.session_id,
                "url_at_error": b.url_at_error,
                "created_at": b.created_at.isoformat(),
            }
            for b in bugs
        ]

    return results
