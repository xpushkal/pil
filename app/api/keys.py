"""Key + org management endpoints.

Two flows:

* ``POST /api/v1/orgs`` — bootstrap an org. Today this is open in dev; in
  prod it'd live behind an admin gate. Sprint 1 keeps it simple.
* ``POST /api/v1/keys`` — issue a key for the caller's org. Returns the
  plaintext exactly once. Requires an existing key, or — for bootstrap —
  an ``X-PIL-Admin-Token`` matching ``PIL_MASTER_ENCRYPTION_KEY``.
* ``POST /api/v1/keys/{id}/rotate`` — mint a successor; the original keeps
  serving traffic until its grace window expires.
* ``GET /api/v1/keys`` — list keys for the caller's org (no secrets).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session, require_pil_key
from app.core.auth import AuthenticatedKey, generate_key, grace_expiry_from_now
from app.db.models import ApiKey, Organization
from app.settings import get_settings
from app.utils.crypto import new_dek, wrap_dek

router = APIRouter(prefix="/api/v1", tags=["keys"])


# ----- schemas --------------------------------------------------------------


class OrgCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    raw_logging_opt_in: bool = False


class OrgOut(BaseModel):
    id: UUID
    name: str
    raw_logging_opt_in: bool


class KeyCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    rate_limit_per_hour: int | None = None


class KeyOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    suffix: str
    rate_limit_per_hour: int
    created_at: datetime
    revoked_at: datetime | None = None
    grace_expires_at: datetime | None = None


class KeyIssued(KeyOut):
    plaintext: str = Field(description="Only returned once on creation/rotation.")


# ----- helpers --------------------------------------------------------------


def _row_to_key_out(row: ApiKey) -> KeyOut:
    return KeyOut(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        suffix=row.key_suffix,
        rate_limit_per_hour=row.rate_limit_per_hour,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        grace_expires_at=row.grace_expires_at,
    )


def _check_admin(token: str | None) -> None:
    expected = get_settings().master_encryption_key.get_secret_value()
    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "ADMIN_REQUIRED"},
        )


# ----- endpoints ------------------------------------------------------------


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreateBody,
    session: AsyncSession = Depends(db_session),
    x_pil_admin_token: str | None = Header(default=None, alias="X-PIL-Admin-Token"),
) -> OrgOut:
    _check_admin(x_pil_admin_token)

    dek = new_dek()
    org = Organization(
        name=body.name,
        raw_logging_opt_in=body.raw_logging_opt_in,
        encryption_key_wrapped=wrap_dek(dek),
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return OrgOut(id=org.id, name=org.name, raw_logging_opt_in=org.raw_logging_opt_in)


@router.post("/orgs/{org_id}/keys", response_model=KeyIssued, status_code=status.HTTP_201_CREATED)
async def issue_org_key(
    org_id: UUID,
    body: KeyCreateBody,
    session: AsyncSession = Depends(db_session),
    x_pil_admin_token: str | None = Header(default=None, alias="X-PIL-Admin-Token"),
) -> KeyIssued:
    """Issue the *first* key for an org. Subsequent keys go through /api/v1/keys."""
    _check_admin(x_pil_admin_token)

    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail={"error": "ORG_NOT_FOUND"})

    issued = generate_key()
    row = ApiKey(
        org_id=org.id,
        name=body.name,
        key_hash=issued.hash,
        key_suffix=issued.suffix,
        rate_limit_per_hour=body.rate_limit_per_hour
        or get_settings().default_rate_limit_per_hour,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    out = _row_to_key_out(row)
    return KeyIssued(**out.model_dump(), plaintext=issued.plaintext)


@router.get("/keys", response_model=list[KeyOut])
async def list_keys(
    auth: AuthenticatedKey = Depends(require_pil_key),
    session: AsyncSession = Depends(db_session),
) -> list[KeyOut]:
    rows = await session.execute(
        select(ApiKey).where(ApiKey.org_id == auth.organization.id)
    )
    return [_row_to_key_out(r) for r in rows.scalars()]


@router.post("/keys", response_model=KeyIssued, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: KeyCreateBody,
    auth: AuthenticatedKey = Depends(require_pil_key),
    session: AsyncSession = Depends(db_session),
) -> KeyIssued:
    issued = generate_key()
    row = ApiKey(
        org_id=auth.organization.id,
        name=body.name,
        key_hash=issued.hash,
        key_suffix=issued.suffix,
        rate_limit_per_hour=body.rate_limit_per_hour
        or auth.api_key.rate_limit_per_hour,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    out = _row_to_key_out(row)
    return KeyIssued(**out.model_dump(), plaintext=issued.plaintext)


@router.post(
    "/keys/{key_id}/rotate",
    response_model=KeyIssued,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_key(
    key_id: UUID,
    auth: AuthenticatedKey = Depends(require_pil_key),
    session: AsyncSession = Depends(db_session),
) -> KeyIssued:
    old = await session.get(ApiKey, key_id)
    if old is None or old.org_id != auth.organization.id:
        raise HTTPException(status_code=404, detail={"error": "KEY_NOT_FOUND"})
    if old.grace_expires_at is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": "ALREADY_ROTATED", "grace_expires_at": old.grace_expires_at.isoformat()},
        )

    issued = generate_key()
    new = ApiKey(
        org_id=old.org_id,
        name=f"{old.name} (rotated)",
        key_hash=issued.hash,
        key_suffix=issued.suffix,
        rate_limit_per_hour=old.rate_limit_per_hour,
        rotated_from_id=old.id,
    )
    old.grace_expires_at = grace_expiry_from_now()
    session.add(new)
    await session.commit()
    await session.refresh(new)
    out = _row_to_key_out(new)
    return KeyIssued(**out.model_dump(), plaintext=issued.plaintext)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: UUID,
    auth: AuthenticatedKey = Depends(require_pil_key),
    session: AsyncSession = Depends(db_session),
) -> None:
    row = await session.get(ApiKey, key_id)
    if row is None or row.org_id != auth.organization.id:
        raise HTTPException(status_code=404, detail={"error": "KEY_NOT_FOUND"})
    row.revoked_at = datetime.now(timezone.utc)
    await session.commit()
