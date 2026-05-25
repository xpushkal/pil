"""SQLAlchemy ORM models.

Schema notes
------------
* ``requests`` stores **metadata only**. Raw prompt/response text only ever
  lands in ``request_payloads`` and only when the owning org has opted in.
* ``semantic_cache`` is declarative-partitioned by (provider, model) at the
  SQL level — see ``app/db/migrations/versions/0001_initial.py`` for the
  partition DDL. The ORM model points at the parent table; reads/writes
  route to leaf partitions automatically via PostgreSQL.
* All timestamps are ``timestamptz`` (UTC).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Project-wide declarative base."""


# ----------------------------------------------------------------------------
# organizations
# ----------------------------------------------------------------------------
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_logging_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Per-org encryption key id; references a row in a (future) KMS table.
    # Sprint 1 stores the wrapped key inline as bytes for simplicity.
    encryption_key_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cache_ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_similarity_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="organization")


# ----------------------------------------------------------------------------
# api_keys (PIL keys — distinct from upstream provider keys)
# ----------------------------------------------------------------------------
class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        Index("ix_api_keys_org_id", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # argon2id hash of the full pil-<uuid> token. We never store the plaintext.
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Last 4 chars of the plaintext for UI display ("pil-...XXXX").
    key_suffix: Mapped[str] = mapped_column(String(8), nullable=False)
    rate_limit_per_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1000")
    )
    # If this key was issued as a rotation of an older one, link back so the
    # old key can serve traffic during the 24h grace window.
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id"), nullable=True
    )
    grace_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="api_keys")


# ----------------------------------------------------------------------------
# requests — metadata only, zero-retention by default
# ----------------------------------------------------------------------------
class RequestLog(Base):
    __tablename__ = "requests"
    __table_args__ = (
        Index("ix_requests_org_id_created_at", "org_id", "created_at"),
        Index("ix_requests_trace_id", "trace_id"),
        CheckConstraint("prompt_tokens >= 0", name="ck_requests_prompt_tokens_nonneg"),
        CheckConstraint("completion_tokens >= 0", name="ck_requests_completion_tokens_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id"), nullable=False
    )
    # sha256 of the scrubbed prompt body. NEVER the raw prompt.
    request_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    estimated_cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    latency_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # category names only — never raw values
    pii_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    pii_entity_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ----------------------------------------------------------------------------
# request_payloads — written only when org has raw_logging_opt_in = true
# ----------------------------------------------------------------------------
class RequestPayload(Base):
    __tablename__ = "request_payloads"
    __table_args__ = (Index("ix_request_payloads_request_id", "request_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # AES-256-GCM. Each ciphertext column stores ``nonce || ciphertext_with_tag``;
    # nonces are random per-encryption to avoid reuse across the two payloads.
    prompt_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ----------------------------------------------------------------------------
# semantic_cache — pgvector, declaratively partitioned by (provider, model)
# ----------------------------------------------------------------------------
class SemanticCacheEntry(Base):
    __tablename__ = "semantic_cache"
    # Composite PK is required because PG partitioned tables must include the
    # partition keys in any unique constraint.
    __table_args__ = (
        Index(
            "ix_semantic_cache_org_provider_model",
            "org_id",
            "provider",
            "model",
        ),
        {"postgresql_partition_by": "LIST (provider)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # sha256 of scrubbed prompt — used as a cheap exact-match short-circuit
    prompt_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    # 384-dim from all-MiniLM-L6-v2
    embedding: Mapped[Any] = mapped_column(Vector(384), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
