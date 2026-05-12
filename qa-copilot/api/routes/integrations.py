"""External Integrations — file bugs to GitHub Issues, Jira, Linear (n8n: integrations)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Integration, Bug

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])

INTEG_TYPES = {"github", "jira", "linear"}


class IntegrationCreate(BaseModel):
    name: str
    integ_type: str
    config: dict[str, Any]  # repo/org/token/project/etc
    active: bool = True


class IntegrationUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    active: bool | None = None


class IntegrationResponse(BaseModel):
    id: str
    name: str
    integ_type: str
    active: bool
    created_at: str
    config_keys: list[str]   # return only key names, not values


def _to_resp(i: Integration) -> IntegrationResponse:
    return IntegrationResponse(
        id=i.id,
        name=i.name,
        integ_type=i.integ_type,
        active=i.active,
        created_at=i.created_at.isoformat(),
        config_keys=list((i.config or {}).keys()),
    )


@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(db: AsyncSession = Depends(get_db)) -> list[IntegrationResponse]:
    result = await db.execute(select(Integration).order_by(Integration.name))
    return [_to_resp(i) for i in result.scalars().all()]


@router.post("", response_model=IntegrationResponse, status_code=201)
async def create_integration(
    body: IntegrationCreate, db: AsyncSession = Depends(get_db)
) -> IntegrationResponse:
    if body.integ_type not in INTEG_TYPES:
        raise HTTPException(422, f"integ_type must be one of {sorted(INTEG_TYPES)}")
    i = Integration(name=body.name, integ_type=body.integ_type, config=body.config, active=body.active)
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return _to_resp(i)


@router.get("/{integ_id}", response_model=IntegrationResponse)
async def get_integration(integ_id: str, db: AsyncSession = Depends(get_db)) -> IntegrationResponse:
    return _to_resp(await _get_or_404(integ_id, db))


@router.patch("/{integ_id}", response_model=IntegrationResponse)
async def update_integration(
    integ_id: str, body: IntegrationUpdate, db: AsyncSession = Depends(get_db)
) -> IntegrationResponse:
    i = await _get_or_404(integ_id, db)
    if body.name is not None:
        i.name = body.name
    if body.config is not None:
        i.config = body.config
    if body.active is not None:
        i.active = body.active
    await db.commit()
    await db.refresh(i)
    return _to_resp(i)


@router.delete("/{integ_id}", status_code=204)
async def delete_integration(integ_id: str, db: AsyncSession = Depends(get_db)) -> None:
    i = await _get_or_404(integ_id, db)
    await db.delete(i)
    await db.commit()


@router.post("/{integ_id}/file-bug/{bug_id}")
async def file_bug(
    integ_id: str, bug_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """File a specific bug as a ticket in the external integration."""
    from services.integrations import file_bug_to_integration

    i = await _get_or_404(integ_id, db)
    if not i.active:
        raise HTTPException(409, "Integration is disabled")

    result = await db.execute(select(Bug).where(Bug.id == bug_id))
    bug = result.scalar_one_or_none()
    if not bug:
        raise HTTPException(404, "Bug not found")

    ticket_url, ticket_id = await file_bug_to_integration(i, bug)
    return {"ticket_url": ticket_url, "ticket_id": ticket_id, "integration": i.integ_type}


@router.post("/{integ_id}/test")
async def test_integration(integ_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Verify the integration credentials are valid."""
    from services.integrations import test_integration_connection
    i = await _get_or_404(integ_id, db)
    ok, detail = await test_integration_connection(i)
    return {"success": ok, "detail": detail}


async def _get_or_404(integ_id: str, db: AsyncSession) -> Integration:
    result = await db.execute(select(Integration).where(Integration.id == integ_id))
    i = result.scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Integration not found")
    return i
