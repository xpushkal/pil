"""drop request_payloads.nonce; ciphertext columns now embed nonce || ct

Revision ID: 0002_payload_nonces
Revises: 0001_initial
Create Date: 2026-05-25 00:10:00.000000

Why
---
AES-GCM requires a unique nonce per (key, message). The initial schema kept a
single ``nonce`` column shared by the prompt and response ciphertexts, which
would force nonce reuse across the two encryptions. We instead embed the
nonce at the front of each ciphertext blob (``nonce || ct``) and drop the
shared column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_payload_nonces"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("request_payloads", "nonce")


def downgrade() -> None:
    op.add_column(
        "request_payloads",
        sa.Column("nonce", sa.LargeBinary(12), nullable=False, server_default=sa.text("'\\x000000000000000000000000'::bytea")),
    )
    op.alter_column("request_payloads", "nonce", server_default=None)
