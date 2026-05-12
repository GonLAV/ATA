"""Inbound Webhooks — trigger QA sessions from external CI/CD pipelines (n8n: webhook triggers)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Schedule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/trigger/{token}", status_code=202)
async def webhook_trigger(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Generic inbound webhook — find the schedule with this token and launch a session.
    Accepts an optional JSON body to override session config.
    """
    result = await db.execute(select(Schedule).where(Schedule.webhook_token == token))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Webhook token not found")
    if not schedule.enabled:
        raise HTTPException(status_code=409, detail="Schedule is disabled")

    # Allow body to carry config overrides
    try:
        body: dict = await request.json()
    except Exception:
        body = {}

    config_override = {**schedule.config, **body.get("config", {})}
    url_override    = body.get("url", schedule.url)

    from db.models import Session as SessionModel, SessionStatus
    from api.routes.sessions import _run_session_background

    session = SessionModel(url=url_override, config=config_override, status=SessionStatus.pending)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    background_tasks.add_task(_run_session_background, session.id)

    logger.info(
        "Webhook trigger: schedule=%s session=%s url=%s",
        schedule.id, session.id, url_override,
    )
    return {
        "session_id": session.id,
        "schedule_id": schedule.id,
        "url": url_override,
        "status": "accepted",
    }


@router.post("/session", status_code=202)
async def generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Bare webhook — POST {url, config} to immediately start a session.
    Useful for ad-hoc CI integration without a saved schedule.
    """
    try:
        body: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")

    url = body.get("url")
    if not url:
        raise HTTPException(status_code=422, detail="'url' is required")

    from db.models import Session as SessionModel, SessionStatus
    from api.routes.sessions import _run_session_background

    session = SessionModel(
        url=str(url),
        config=body.get("config", {}),
        status=SessionStatus.pending,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    background_tasks.add_task(_run_session_background, session.id)

    logger.info("Generic webhook: session=%s url=%s", session.id, url)
    return {"session_id": session.id, "url": str(url), "status": "accepted"}
