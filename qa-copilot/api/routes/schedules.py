"""Scheduled QA sessions — cron-triggered automation (n8n: scheduled triggers)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Schedule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schedules", tags=["schedules"])


class ScheduleCreate(BaseModel):
    name: str
    url: HttpUrl
    cron_expr: str
    config: dict[str, Any] = {}
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    url: HttpUrl | None = None
    cron_expr: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ScheduleResponse(BaseModel):
    id: str
    name: str
    url: str
    cron_expr: str
    config: dict[str, Any]
    enabled: bool
    webhook_token: str | None
    last_run_at: str | None
    next_run_at: str | None
    last_session_id: str | None
    created_at: str


def _to_resp(s: Schedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=s.id,
        name=s.name,
        url=s.url,
        cron_expr=s.cron_expr,
        config=s.config or {},
        enabled=s.enabled,
        webhook_token=s.webhook_token,
        last_run_at=s.last_run_at.isoformat() if s.last_run_at else None,
        next_run_at=s.next_run_at.isoformat() if s.next_run_at else None,
        last_session_id=s.last_session_id,
        created_at=s.created_at.isoformat(),
    )


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(db: AsyncSession = Depends(get_db)) -> list[ScheduleResponse]:
    result = await db.execute(select(Schedule).order_by(Schedule.created_at.desc()))
    return [_to_resp(s) for s in result.scalars().all()]


@router.post("", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    body: ScheduleCreate, db: AsyncSession = Depends(get_db)
) -> ScheduleResponse:
    import secrets
    _validate_cron(body.cron_expr)
    s = Schedule(
        name=body.name,
        url=str(body.url),
        cron_expr=body.cron_expr,
        config=body.config,
        enabled=body.enabled,
        webhook_token=secrets.token_urlsafe(24),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    _register_cron(s)
    logger.info("Created schedule %s (%s)", s.id, s.name)
    return _to_resp(s)


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)) -> ScheduleResponse:
    return _to_resp(await _get_or_404(schedule_id, db))


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str, body: ScheduleUpdate, db: AsyncSession = Depends(get_db)
) -> ScheduleResponse:
    s = await _get_or_404(schedule_id, db)
    if body.name is not None:
        s.name = body.name
    if body.url is not None:
        s.url = str(body.url)
    if body.cron_expr is not None:
        _validate_cron(body.cron_expr)
        s.cron_expr = body.cron_expr
    if body.config is not None:
        s.config = body.config
    if body.enabled is not None:
        s.enabled = body.enabled
    await db.commit()
    await db.refresh(s)
    _register_cron(s)
    return _to_resp(s)


@router.post("/{schedule_id}/enable", response_model=ScheduleResponse)
async def enable_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)) -> ScheduleResponse:
    s = await _get_or_404(schedule_id, db)
    s.enabled = True
    await db.commit()
    await db.refresh(s)
    _register_cron(s)
    return _to_resp(s)


@router.post("/{schedule_id}/disable", response_model=ScheduleResponse)
async def disable_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)) -> ScheduleResponse:
    s = await _get_or_404(schedule_id, db)
    s.enabled = False
    await db.commit()
    await db.refresh(s)
    _unregister_cron(s.id)
    return _to_resp(s)


@router.post("/{schedule_id}/run-now", status_code=202)
async def run_schedule_now(
    schedule_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Immediately trigger the schedule — useful for testing."""
    from agent.scheduler import trigger_schedule_now
    s = await _get_or_404(schedule_id, db)
    session_id = await trigger_schedule_now(s)
    return {"session_id": session_id, "schedule_id": schedule_id}


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)) -> None:
    s = await _get_or_404(schedule_id, db)
    _unregister_cron(s.id)
    await db.delete(s)
    await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_cron(expr: str) -> None:
    parts = expr.strip().split()
    if len(parts) not in (5, 6):
        raise HTTPException(
            status_code=422,
            detail="cron_expr must have 5 fields (min hour dom mon dow) or 6 (with seconds)",
        )


def _register_cron(s: Schedule) -> None:
    try:
        from agent.scheduler import register_schedule
        register_schedule(s)
    except Exception as exc:
        logger.warning("Could not register cron for %s: %s", s.id, exc)


def _unregister_cron(schedule_id: str) -> None:
    try:
        from agent.scheduler import unregister_schedule
        unregister_schedule(schedule_id)
    except Exception as exc:
        logger.warning("Could not unregister cron for %s: %s", schedule_id, exc)


async def _get_or_404(schedule_id: str, db: AsyncSession) -> Schedule:
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return s
