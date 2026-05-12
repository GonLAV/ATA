"""Global Variables — reusable key-value pairs (n8n: global variables)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Variable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/variables", tags=["variables"])


class VariableCreate(BaseModel):
    key: str
    value: str
    description: str = ""


class VariableUpdate(BaseModel):
    value: str | None = None
    description: str | None = None


class VariableResponse(BaseModel):
    id: str
    key: str
    value: str
    description: str
    created_at: str


def _to_resp(v: Variable) -> VariableResponse:
    return VariableResponse(
        id=v.id,
        key=v.key,
        value=v.value,
        description=v.description or "",
        created_at=v.created_at.isoformat(),
    )


@router.get("", response_model=list[VariableResponse])
async def list_variables(db: AsyncSession = Depends(get_db)) -> list[VariableResponse]:
    result = await db.execute(select(Variable).order_by(Variable.key))
    return [_to_resp(v) for v in result.scalars().all()]


@router.post("", response_model=VariableResponse, status_code=201)
async def create_variable(
    body: VariableCreate, db: AsyncSession = Depends(get_db)
) -> VariableResponse:
    v = Variable(key=body.key, value=body.value, description=body.description)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return _to_resp(v)


@router.get("/{variable_id}", response_model=VariableResponse)
async def get_variable(variable_id: str, db: AsyncSession = Depends(get_db)) -> VariableResponse:
    return _to_resp(await _get_or_404(variable_id, db))


@router.patch("/{variable_id}", response_model=VariableResponse)
async def update_variable(
    variable_id: str, body: VariableUpdate, db: AsyncSession = Depends(get_db)
) -> VariableResponse:
    v = await _get_or_404(variable_id, db)
    if body.value is not None:
        v.value = body.value
    if body.description is not None:
        v.description = body.description
    await db.commit()
    await db.refresh(v)
    return _to_resp(v)


@router.delete("/{variable_id}", status_code=204)
async def delete_variable(variable_id: str, db: AsyncSession = Depends(get_db)) -> None:
    v = await _get_or_404(variable_id, db)
    await db.delete(v)
    await db.commit()


async def _get_or_404(variable_id: str, db: AsyncSession) -> Variable:
    result = await db.execute(select(Variable).where(Variable.id == variable_id))
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Variable not found")
    return v
