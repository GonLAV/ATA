"""Test Templates — save named URL+config combos, launch with one click."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Template

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    url: HttpUrl
    config: dict[str, Any] = {}
    tags: list[str] = []


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    url: HttpUrl | None = None
    config: dict[str, Any] | None = None
    tags: list[str] | None = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    url: str
    config: dict[str, Any]
    tags: list[str]
    use_count: int
    created_at: str
    updated_at: str


def _to_resp(t: Template) -> TemplateResponse:
    return TemplateResponse(
        id=t.id,
        name=t.name,
        description=t.description or "",
        url=t.url,
        config=t.config or {},
        tags=t.tags or [],
        use_count=t.use_count or 0,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat() if t.updated_at else t.created_at.isoformat(),
    )


@router.get("", response_model=list[TemplateResponse])
async def list_templates(db: AsyncSession = Depends(get_db)) -> list[TemplateResponse]:
    result = await db.execute(select(Template).order_by(Template.created_at.desc()))
    return [_to_resp(t) for t in result.scalars().all()]


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: TemplateCreate, db: AsyncSession = Depends(get_db)
) -> TemplateResponse:
    t = Template(
        name=body.name,
        description=body.description,
        url=str(body.url),
        config=body.config,
        tags=body.tags,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    logger.info("Created template %s (%s)", t.id, t.name)
    return _to_resp(t)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)) -> TemplateResponse:
    t = await _get_or_404(template_id, db)
    return _to_resp(t)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str, body: TemplateUpdate, db: AsyncSession = Depends(get_db)
) -> TemplateResponse:
    t = await _get_or_404(template_id, db)
    if body.name is not None:
        t.name = body.name
    if body.description is not None:
        t.description = body.description
    if body.url is not None:
        t.url = str(body.url)
    if body.config is not None:
        t.config = body.config
    if body.tags is not None:
        t.tags = body.tags
    await db.commit()
    await db.refresh(t)
    return _to_resp(t)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str, db: AsyncSession = Depends(get_db)) -> None:
    t = await _get_or_404(template_id, db)
    await db.delete(t)
    await db.commit()


@router.post("/{template_id}/launch", status_code=201)
async def launch_template(
    template_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Create a new QA session from this template."""
    from db.models import Session as SessionModel, SessionStatus
    from api.routes.sessions import _run_session_background

    t = await _get_or_404(template_id, db)
    session = SessionModel(url=t.url, config=t.config, status=SessionStatus.pending)
    db.add(session)
    t.use_count = (t.use_count or 0) + 1
    await db.commit()
    await db.refresh(session)
    background_tasks.add_task(_run_session_background, session.id)
    logger.info("Launched session %s from template %s", session.id, template_id)
    return {"session_id": session.id, "template_id": template_id}


async def _get_or_404(template_id: str, db: AsyncSession) -> Template:
    result = await db.execute(select(Template).where(Template.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t
