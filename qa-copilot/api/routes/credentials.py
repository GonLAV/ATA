"""Credentials Store — stored secrets for authenticated testing (n8n: credentials)."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Credential

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credentials"])

CRED_TYPES = {"basic", "bearer", "cookie", "api_key", "oauth2"}


class CredentialCreate(BaseModel):
    name: str
    cred_type: str
    data: dict[str, Any]
    description: str = ""


class CredentialUpdate(BaseModel):
    name: str | None = None
    data: dict[str, Any] | None = None
    description: str | None = None


class CredentialResponse(BaseModel):
    id: str
    name: str
    cred_type: str
    description: str
    created_at: str
    # data is intentionally omitted from responses (never send secrets back)


def _to_resp(c: Credential) -> CredentialResponse:
    return CredentialResponse(
        id=c.id,
        name=c.name,
        cred_type=c.cred_type,
        description=c.description or "",
        created_at=c.created_at.isoformat(),
    )


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(db: AsyncSession = Depends(get_db)) -> list[CredentialResponse]:
    result = await db.execute(select(Credential).order_by(Credential.name))
    return [_to_resp(c) for c in result.scalars().all()]


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CredentialCreate, db: AsyncSession = Depends(get_db)
) -> CredentialResponse:
    if body.cred_type not in CRED_TYPES:
        raise HTTPException(status_code=422, detail=f"cred_type must be one of {sorted(CRED_TYPES)}")
    c = Credential(
        name=body.name,
        cred_type=body.cred_type,
        data=_encrypt(body.data),
        description=body.description,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _to_resp(c)


@router.get("/{cred_id}", response_model=CredentialResponse)
async def get_credential(cred_id: str, db: AsyncSession = Depends(get_db)) -> CredentialResponse:
    return _to_resp(await _get_or_404(cred_id, db))


@router.patch("/{cred_id}", response_model=CredentialResponse)
async def update_credential(
    cred_id: str, body: CredentialUpdate, db: AsyncSession = Depends(get_db)
) -> CredentialResponse:
    c = await _get_or_404(cred_id, db)
    if body.name is not None:
        c.name = body.name
    if body.data is not None:
        c.data = _encrypt(body.data)
    if body.description is not None:
        c.description = body.description
    await db.commit()
    await db.refresh(c)
    return _to_resp(c)


@router.delete("/{cred_id}", status_code=204)
async def delete_credential(cred_id: str, db: AsyncSession = Depends(get_db)) -> None:
    c = await _get_or_404(cred_id, db)
    await db.delete(c)
    await db.commit()


def get_decrypted(cred: Credential) -> dict[str, Any]:
    """Used internally by the test runner to retrieve credential data."""
    return _decrypt(cred.data)


# ---------------------------------------------------------------------------
# Simple symmetric encryption using Fernet (cryptography package)
# Falls back to plaintext storage when key is not configured.
# ---------------------------------------------------------------------------

def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        from config import settings
        key = getattr(settings, "credential_encryption_key", None)
        if key:
            return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        pass
    return None


def _encrypt(data: dict) -> dict:
    f = _get_fernet()
    if f:
        token = f.encrypt(json.dumps(data).encode()).decode()
        return {"_enc": token}
    return data


def _decrypt(data: dict) -> dict:
    if "_enc" not in data:
        return data
    f = _get_fernet()
    if f:
        return json.loads(f.decrypt(data["_enc"].encode()))
    return data


async def _get_or_404(cred_id: str, db: AsyncSession) -> Credential:
    result = await db.execute(select(Credential).where(Credential.id == cred_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Credential not found")
    return c
