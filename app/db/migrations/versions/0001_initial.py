"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25 00:00:00.000000

Creates:
  * extensions: pgcrypto (for gen_random_uuid) and vector (pgvector)
  * organizations, api_keys, requests, request_payloads
  * semantic_cache — declarative-partitioned (provider) -> (model) with
    HNSW index on each leaf partition. Default partitions absorb unknown
    providers/models so writes never fail; the cache writer creates dedicated
    leaf partitions on demand in Phase 2.

Forward + backward round-trip is tested in CI.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRE_WARMED_PARTITIONS = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
    "anthropic": [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ],
    "gemini": ["gemini-1.5-pro", "gemini-1.5-flash"],
}


def _safe(name: str) -> str:
    """Turn provider/model names into postgres-identifier-safe suffixes."""
    return name.replace("-", "_").replace(".", "_").replace(":", "_").lower()


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # organizations
    # ------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("raw_logging_opt_in", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("encryption_key_wrapped", sa.LargeBinary, nullable=True),
        sa.Column("cache_ttl_seconds", sa.Integer, nullable=True),
        sa.Column("cache_similarity_threshold", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.Text, nullable=False),
        sa.Column("key_suffix", sa.String(8), nullable=False),
        sa.Column("rate_limit_per_hour", sa.Integer, nullable=False,
                  server_default=sa.text("1000")),
        sa.Column("rotated_from_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id"), nullable=True),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])

    # ------------------------------------------------------------------
    # requests (metadata only)
    # ------------------------------------------------------------------
    op.create_table(
        "requests",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_key_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("estimated_cost_usd", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.BigInteger, nullable=False),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("pii_categories", sa.dialects.postgresql.ARRAY(sa.String(64)),
                  nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("pii_entity_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("prompt_tokens >= 0", name="ck_requests_prompt_tokens_nonneg"),
        sa.CheckConstraint("completion_tokens >= 0",
                           name="ck_requests_completion_tokens_nonneg"),
    )
    op.create_index("ix_requests_org_id_created_at", "requests", ["org_id", "created_at"])
    op.create_index("ix_requests_trace_id", "requests", ["trace_id"])

    # ------------------------------------------------------------------
    # request_payloads (opt-in only; encrypted at rest)
    # ------------------------------------------------------------------
    op.create_table(
        "request_payloads",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("requests.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("prompt_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("response_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_request_payloads_request_id", "request_payloads", ["request_id"])

    # ------------------------------------------------------------------
    # semantic_cache: parent (LIST partitioned by provider)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE semantic_cache (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            provider varchar(64) NOT NULL,
            model varchar(128) NOT NULL,
            org_id uuid NOT NULL,
            prompt_hash bytea NOT NULL,
            embedding vector(384) NOT NULL,
            response_payload jsonb NOT NULL,
            prompt_tokens int,
            completion_tokens int,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (id, provider, model)
        ) PARTITION BY LIST (provider);
        """
    )

    # Per-provider sub-partitioned tables (further partitioned by model).
    for provider in PRE_WARMED_PARTITIONS:
        op.execute(
            f"""
            CREATE TABLE semantic_cache_{_safe(provider)}
              PARTITION OF semantic_cache
              FOR VALUES IN ('{provider}')
              PARTITION BY LIST (model);
            """
        )

    # Top-level default partition for unknown providers (not sub-partitioned).
    op.execute(
        "CREATE TABLE semantic_cache_default PARTITION OF semantic_cache DEFAULT;"
    )
    # Index the default partition directly.
    op.execute(
        """
        CREATE INDEX ix_semantic_cache_default_org_provider_model
          ON semantic_cache_default (org_id, provider, model);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_semantic_cache_default_embedding
          ON semantic_cache_default USING hnsw (embedding vector_cosine_ops);
        """
    )

    # Pre-warmed model partitions for each known provider.
    for provider, models in PRE_WARMED_PARTITIONS.items():
        psafe = _safe(provider)
        for model in models:
            msafe = _safe(model)
            tbl = f"semantic_cache_{psafe}_{msafe}"
            op.execute(
                f"""
                CREATE TABLE {tbl}
                  PARTITION OF semantic_cache_{psafe}
                  FOR VALUES IN ('{model}');
                """
            )
            op.execute(
                f"""
                CREATE INDEX ix_{tbl}_org_model
                  ON {tbl} (org_id, model);
                """
            )
            op.execute(
                f"""
                CREATE INDEX ix_{tbl}_embedding
                  ON {tbl} USING hnsw (embedding vector_cosine_ops);
                """
            )
        # Default leaf partition for unknown models within this provider.
        leaf_default = f"semantic_cache_{psafe}_default"
        op.execute(
            f"""
            CREATE TABLE {leaf_default}
              PARTITION OF semantic_cache_{psafe} DEFAULT;
            """
        )
        op.execute(
            f"""
            CREATE INDEX ix_{leaf_default}_org_model
              ON {leaf_default} (org_id, model);
            """
        )
        op.execute(
            f"""
            CREATE INDEX ix_{leaf_default}_embedding
              ON {leaf_default} USING hnsw (embedding vector_cosine_ops);
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS semantic_cache CASCADE")
    op.execute("DROP TABLE IF EXISTS request_payloads CASCADE")
    op.execute("DROP TABLE IF EXISTS requests CASCADE")
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")
    # Leave extensions in place — other tenants of the DB may rely on them.
