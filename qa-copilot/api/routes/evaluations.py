"""Evaluation API — run accuracy scoring, retrieve results, compare sessions."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Evaluation, EvaluationFeedback, EvaluationStatus, Session as SessionModel, SessionStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evaluations", tags=["evaluations"])


# ── Pydantic I/O schemas ──────────────────────────────────────────────────────

class RunEvaluationRequest(BaseModel):
    expected_bugs: list[str] | None = None   # ground-truth bug titles (optional)
    use_ai_summary: bool = True


class FeedbackItem(BaseModel):
    id: str
    dimension: str
    category: str
    severity: str
    priority: int
    title: str
    detail: str
    suggestion: str
    affected_ids: list[str]


class BugScoreItem(BaseModel):
    bug_id: str
    title: str
    score: float
    issues: list[str]
    suggestions: list[str]


class EvaluationResponse(BaseModel):
    id: str
    session_id: str
    status: str
    overall_score: float | None
    accuracy_pct: float | None
    grade: str | None
    # Dimension breakdown
    dimensions: dict[str, float | None]
    # Per-bug scores
    bug_scores: list[BugScoreItem]
    # Feedback
    feedback: list[FeedbackItem]
    # Ground-truth (optional)
    gt_expected_bugs: int | None
    gt_found_bugs: int | None
    gt_recall_pct: float | None
    gt_precision_pct: float | None
    gt_f1_score: float | None
    # AI summary
    ai_summary: str | None
    created_at: str
    completed_at: str | None


def _to_resp(ev: Evaluation) -> EvaluationResponse:
    feedback = [
        FeedbackItem(
            id=f.id, dimension=f.dimension, category=f.category,
            severity=f.severity, priority=f.priority,
            title=f.title, detail=f.detail, suggestion=f.suggestion or "",
            affected_ids=f.affected_ids or [],
        )
        for f in (ev.feedback or [])
    ]
    bug_scores = [
        BugScoreItem(
            bug_id=b["bug_id"], title=b["title"], score=b["score"],
            issues=b.get("issues", []), suggestions=b.get("suggestions", []),
        )
        for b in (ev.bug_scores or [])
    ]
    return EvaluationResponse(
        id=ev.id,
        session_id=ev.session_id,
        status=ev.status.value if ev.status else "pending",
        overall_score=ev.overall_score,
        accuracy_pct=ev.accuracy_pct,
        grade=ev.grade,
        dimensions={
            "coverage":    ev.dim_coverage,
            "bug_quality": ev.dim_bug_quality,
            "precision":   ev.dim_precision,
            "detail":      ev.dim_detail,
            "severity":    ev.dim_severity,
            "confidence":  ev.dim_confidence,
            "monitoring":  ev.dim_monitoring,
            "persona_div": ev.dim_persona_div,
        },
        bug_scores=bug_scores,
        feedback=feedback,
        gt_expected_bugs=ev.gt_expected_bugs,
        gt_found_bugs=ev.gt_found_bugs,
        gt_recall_pct=ev.gt_recall_pct,
        gt_precision_pct=ev.gt_precision_pct,
        gt_f1_score=ev.gt_f1_score,
        ai_summary=ev.ai_summary,
        created_at=ev.created_at.isoformat(),
        completed_at=ev.completed_at.isoformat() if ev.completed_at else None,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}", response_model=EvaluationResponse, status_code=202)
async def run_evaluation(
    session_id: str,
    body: RunEvaluationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> EvaluationResponse:
    """
    Trigger an evaluation for a completed session.
    Returns immediately with status=running; result is ready once background task finishes.
    """
    sess_res = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = sess_res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.status not in (SessionStatus.completed, SessionStatus.failed):
        raise HTTPException(409, "Session must be completed before evaluation")

    # Create or reset the evaluation row
    ev_res = await db.execute(select(Evaluation).where(Evaluation.session_id == session_id))
    ev = ev_res.scalar_one_or_none()
    if not ev:
        ev = Evaluation(session_id=session_id)
        db.add(ev)
    ev.status = EvaluationStatus.running
    ev.overall_score = None
    await db.commit()
    await db.refresh(ev)

    background_tasks.add_task(
        _run_in_background, session_id, body.expected_bugs, body.use_ai_summary
    )
    logger.info("Queued evaluation for session %s", session_id)
    return _to_resp(ev)


@router.get("/sessions/{session_id}", response_model=EvaluationResponse)
async def get_evaluation(session_id: str, db: AsyncSession = Depends(get_db)) -> EvaluationResponse:
    """Retrieve the latest evaluation result for a session."""
    ev = await _get_or_404(session_id, db)
    return _to_resp(ev)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_evaluation(session_id: str, db: AsyncSession = Depends(get_db)) -> None:
    ev = await _get_or_404(session_id, db)
    await db.delete(ev)
    await db.commit()


@router.get("/sessions/{session_id}/feedback", response_model=list[FeedbackItem])
async def get_feedback(
    session_id: str,
    severity: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackItem]:
    """Return feedback items, optionally filtered by severity."""
    ev = await _get_or_404(session_id, db)
    items = ev.feedback or []
    if severity:
        items = [f for f in items if f.severity == severity]
    return [
        FeedbackItem(
            id=f.id, dimension=f.dimension, category=f.category,
            severity=f.severity, priority=f.priority,
            title=f.title, detail=f.detail, suggestion=f.suggestion or "",
            affected_ids=f.affected_ids or [],
        )
        for f in sorted(items, key=lambda x: x.priority, reverse=True)
    ]


@router.get("/sessions/{session_id}/bug-scores", response_model=list[BugScoreItem])
async def get_bug_scores(session_id: str, db: AsyncSession = Depends(get_db)) -> list[BugScoreItem]:
    """Return per-bug quality scores."""
    ev = await _get_or_404(session_id, db)
    return sorted(
        [BugScoreItem(bug_id=b["bug_id"], title=b["title"], score=b["score"],
                      issues=b.get("issues", []), suggestions=b.get("suggestions", []))
         for b in (ev.bug_scores or [])],
        key=lambda x: x.score,
    )


@router.post("/compare")
async def compare_sessions(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Compare evaluations of two sessions side-by-side.
    Body: {session_a: <id>, session_b: <id>}
    """
    id_a = body.get("session_a")
    id_b = body.get("session_b")
    if not id_a or not id_b:
        raise HTTPException(422, "Both session_a and session_b are required")

    ev_a_res = await db.execute(select(Evaluation).where(Evaluation.session_id == id_a))
    ev_b_res = await db.execute(select(Evaluation).where(Evaluation.session_id == id_b))
    ev_a = ev_a_res.scalar_one_or_none()
    ev_b = ev_b_res.scalar_one_or_none()

    if not ev_a:
        raise HTTPException(404, f"No evaluation found for session {id_a}")
    if not ev_b:
        raise HTTPException(404, f"No evaluation found for session {id_b}")

    dims = ["coverage", "bug_quality", "precision", "detail", "severity", "confidence", "monitoring", "persona_div"]
    dim_map = {
        "coverage": (ev_a.dim_coverage, ev_b.dim_coverage),
        "bug_quality": (ev_a.dim_bug_quality, ev_b.dim_bug_quality),
        "precision": (ev_a.dim_precision, ev_b.dim_precision),
        "detail": (ev_a.dim_detail, ev_b.dim_detail),
        "severity": (ev_a.dim_severity, ev_b.dim_severity),
        "confidence": (ev_a.dim_confidence, ev_b.dim_confidence),
        "monitoring": (ev_a.dim_monitoring, ev_b.dim_monitoring),
        "persona_div": (ev_a.dim_persona_div, ev_b.dim_persona_div),
    }

    comparison = []
    for dim in dims:
        a_score, b_score = dim_map[dim]
        delta = (b_score or 0) - (a_score or 0)
        comparison.append({
            "dimension": dim,
            "session_a": a_score,
            "session_b": b_score,
            "delta": round(delta, 1),
            "winner": "b" if delta > 0 else ("a" if delta < 0 else "tie"),
        })

    return {
        "session_a": {"id": id_a, "score": ev_a.overall_score, "grade": ev_a.grade},
        "session_b": {"id": id_b, "score": ev_b.overall_score, "grade": ev_b.grade},
        "winner": "b" if (ev_b.overall_score or 0) > (ev_a.overall_score or 0) else
                  "a" if (ev_a.overall_score or 0) > (ev_b.overall_score or 0) else "tie",
        "overall_delta": round((ev_b.overall_score or 0) - (ev_a.overall_score or 0), 1),
        "dimensions": comparison,
    }


# ── Background task ───────────────────────────────────────────────────────────

async def _run_in_background(
    session_id: str,
    expected_bugs: list[str] | None,
    use_ai_summary: bool,
) -> None:
    from db.database import AsyncSessionLocal
    from agent.evaluator import run_evaluation

    async with AsyncSessionLocal() as db:
        try:
            await run_evaluation(
                session_id, db,
                expected_bugs=expected_bugs,
                use_ai_summary=use_ai_summary,
            )
            logger.info("Evaluation completed for session %s", session_id)
        except Exception as exc:
            logger.error("Evaluation failed for %s: %s", session_id, exc, exc_info=True)
            # Mark as failed in DB
            ev_res = await db.execute(select(Evaluation).where(Evaluation.session_id == session_id))
            ev = ev_res.scalar_one_or_none()
            if ev:
                ev.status = EvaluationStatus.failed
                ev.error_message = str(exc)
                await db.commit()


async def _get_or_404(session_id: str, db: AsyncSession) -> Evaluation:
    ev_res = await db.execute(select(Evaluation).where(Evaluation.session_id == session_id))
    ev = ev_res.scalar_one_or_none()
    if not ev:
        raise HTTPException(404, "Evaluation not found — run POST /api/evaluations/sessions/{id} first")
    return ev
