"""Notification Targets — outbound alerts on session events (n8n: alerting)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import NotificationTarget

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

TARGET_TYPES  = {"webhook", "slack", "discord", "email"}
VALID_EVENTS  = {
    "session_completed", "session_failed", "session_cancelled",
    "bug_critical", "bug_high", "bug_found",
}
SEVERITIES    = {"low", "medium", "high", "critical"}


class NotificationCreate(BaseModel):
    name: str
    target_type: str
    url: str | None = None
    email: str | None = None
    events: list[str] = ["session_completed", "bug_critical"]
    min_severity: str = "medium"
    active: bool = True


class NotificationUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    email: str | None = None
    events: list[str] | None = None
    min_severity: str | None = None
    active: bool | None = None


class NotificationResponse(BaseModel):
    id: str
    name: str
    target_type: str
    url: str | None
    email: str | None
    events: list[str]
    min_severity: str
    active: bool
    created_at: str


def _to_resp(n: NotificationTarget) -> NotificationResponse:
    return NotificationResponse(
        id=n.id,
        name=n.name,
        target_type=n.target_type,
        url=n.url,
        email=n.email,
        events=n.events or [],
        min_severity=n.min_severity,
        active=n.active,
        created_at=n.created_at.isoformat(),
    )


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(db: AsyncSession = Depends(get_db)) -> list[NotificationResponse]:
    result = await db.execute(select(NotificationTarget).order_by(NotificationTarget.name))
    return [_to_resp(n) for n in result.scalars().all()]


@router.post("", response_model=NotificationResponse, status_code=201)
async def create_notification(
    body: NotificationCreate, db: AsyncSession = Depends(get_db)
) -> NotificationResponse:
    _validate(body.target_type, body.events, body.min_severity)
    n = NotificationTarget(
        name=body.name,
        target_type=body.target_type,
        url=body.url,
        email=body.email,
        events=body.events,
        min_severity=body.min_severity,
        active=body.active,
    )
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return _to_resp(n)


@router.get("/{notif_id}", response_model=NotificationResponse)
async def get_notification(notif_id: str, db: AsyncSession = Depends(get_db)) -> NotificationResponse:
    return _to_resp(await _get_or_404(notif_id, db))


@router.patch("/{notif_id}", response_model=NotificationResponse)
async def update_notification(
    notif_id: str, body: NotificationUpdate, db: AsyncSession = Depends(get_db)
) -> NotificationResponse:
    n = await _get_or_404(notif_id, db)
    if body.name is not None:
        n.name = body.name
    if body.url is not None:
        n.url = body.url
    if body.email is not None:
        n.email = body.email
    if body.events is not None:
        n.events = body.events
    if body.min_severity is not None:
        n.min_severity = body.min_severity
    if body.active is not None:
        n.active = body.active
    await db.commit()
    await db.refresh(n)
    return _to_resp(n)


@router.delete("/{notif_id}", status_code=204)
async def delete_notification(notif_id: str, db: AsyncSession = Depends(get_db)) -> None:
    n = await _get_or_404(notif_id, db)
    await db.delete(n)
    await db.commit()


@router.post("/{notif_id}/test", status_code=200)
async def test_notification(
    notif_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Send a test ping to verify the notification target is reachable."""
    from services.notifier import send_test_notification
    n = await _get_or_404(notif_id, db)
    ok, detail = await send_test_notification(n)
    return {"success": ok, "detail": detail}


def _validate(target_type: str, events: list[str], min_severity: str) -> None:
    if target_type not in TARGET_TYPES:
        raise HTTPException(422, f"target_type must be one of {sorted(TARGET_TYPES)}")
    bad = [e for e in events if e not in VALID_EVENTS]
    if bad:
        raise HTTPException(422, f"Unknown events: {bad}. Valid: {sorted(VALID_EVENTS)}")
    if min_severity not in SEVERITIES:
        raise HTTPException(422, f"min_severity must be one of {sorted(SEVERITIES)}")


async def _get_or_404(notif_id: str, db: AsyncSession) -> NotificationTarget:
    result = await db.execute(select(NotificationTarget).where(NotificationTarget.id == notif_id))
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification target not found")
    return n
