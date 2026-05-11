from __future__ import annotations

import asyncio
import logging
from typing import Any
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.coordinator import SessionCoordinator
from db.database import get_db
from db.models import Session as SessionModel, SessionStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    url: HttpUrl
    config: dict[str, Any] = {}


class SessionResponse(BaseModel):
    id: str
    url: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


def _to_response(s: SessionModel) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        url=s.url,
        status=s.status.value,
        created_at=s.created_at.isoformat(),
        started_at=s.started_at.isoformat() if s.started_at else None,
        completed_at=s.completed_at.isoformat() if s.completed_at else None,
        error_message=s.error_message,
    )


async def _run_session_background(session_id: str) -> None:
    """Runs in a background task — creates its own DB session."""
    from db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session:
            coordinator = SessionCoordinator(db)
            await coordinator.run_session(session)


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Start a new QA Copilot session for the given URL."""
    session = SessionModel(
        url=str(body.url),
        config=body.config,
        status=SessionStatus.pending,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    background_tasks.add_task(_run_session_background, session.id)
    logger.info("Created session %s for %s", session.id, session.url)
    return _to_response(session)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
    offset: int = 0,
) -> list[SessionResponse]:
    result = await db.execute(
        select(SessionModel)
        .order_by(SessionModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_to_response(s) for s in result.scalars().all()]


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> SessionResponse:
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _to_response(session)


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.delete(session)
    await db.commit()
