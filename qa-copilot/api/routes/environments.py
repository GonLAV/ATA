"""Test Environments — named configs for dev / staging / prod (n8n: environments)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Environment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/environments", tags=["environments"])


class EnvironmentCreate(BaseModel):
    name: str
    base_url: HttpUrl
    description: str = ""
    variables: dict[str, str] = {}
    is_default: bool = False


class EnvironmentUpdate(BaseModel):
    name: str | None = None
    base_url: HttpUrl | None = None
    description: str | None = None
    variables: dict[str, str] | None = None
    is_default: bool | None = None


class EnvironmentResponse(BaseModel):
    id: str
    name: str
    base_url: str
    description: str
    variables: dict[str, str]
    is_default: bool
    created_at: str


def _to_resp(e: Environment) -> EnvironmentResponse:
    return EnvironmentResponse(
        id=e.id,
        name=e.name,
        base_url=e.base_url,
        description=e.description or "",
        variables=e.variables or {},
        is_default=e.is_default,
        created_at=e.created_at.isoformat(),
    )


@router.get("", response_model=list[EnvironmentResponse])
async def list_environments(db: AsyncSession = Depends(get_db)) -> list[EnvironmentResponse]:
    result = await db.execute(select(Environment).order_by(Environment.name))
    return [_to_resp(e) for e in result.scalars().all()]


@router.post("", response_model=EnvironmentResponse, status_code=201)
async def create_environment(
    body: EnvironmentCreate, db: AsyncSession = Depends(get_db)
) -> EnvironmentResponse:
    if body.is_default:
        await _clear_default(db)
    e = Environment(
        name=body.name,
        base_url=str(body.base_url),
        description=body.description,
        variables=body.variables,
        is_default=body.is_default,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _to_resp(e)


@router.get("/{env_id}", response_model=EnvironmentResponse)
async def get_environment(env_id: str, db: AsyncSession = Depends(get_db)) -> EnvironmentResponse:
    return _to_resp(await _get_or_404(env_id, db))


@router.patch("/{env_id}", response_model=EnvironmentResponse)
async def update_environment(
    env_id: str, body: EnvironmentUpdate, db: AsyncSession = Depends(get_db)
) -> EnvironmentResponse:
    e = await _get_or_404(env_id, db)
    if body.is_default:
        await _clear_default(db)
    if body.name is not None:
        e.name = body.name
    if body.base_url is not None:
        e.base_url = str(body.base_url)
    if body.description is not None:
        e.description = body.description
    if body.variables is not None:
        e.variables = body.variables
    if body.is_default is not None:
        e.is_default = body.is_default
    await db.commit()
    await db.refresh(e)
    return _to_resp(e)


@router.delete("/{env_id}", status_code=204)
async def delete_environment(env_id: str, db: AsyncSession = Depends(get_db)) -> None:
    e = await _get_or_404(env_id, db)
    await db.delete(e)
    await db.commit()


async def _clear_default(db: AsyncSession) -> None:
    result = await db.execute(select(Environment).where(Environment.is_default == True))  # noqa: E712
    for env in result.scalars().all():
        env.is_default = False
    await db.flush()


async def _get_or_404(env_id: str, db: AsyncSession) -> Environment:
    result = await db.execute(select(Environment).where(Environment.id == env_id))
    e = result.scalar_one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Environment not found")
    return e
