"""Datenschlüssel je Familie (family.wrapped_key); bestehende Familien bekommen beim Upgrade ihren Schlüssel

Braucht KIDCTL_MASTER_KEY (Entwicklung: fester Testschlüssel).

Revision: 0004
Vorgänger: 0003
Erstellt: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app import crypto

    op.add_column("family", sa.Column("wrapped_key", sa.LargeBinary(), nullable=True))
    conn = op.get_bind()
    for (fid,) in conn.execute(sa.text("SELECT id FROM family WHERE wrapped_key IS NULL")).all():
        conn.execute(sa.text("UPDATE family SET wrapped_key = :k WHERE id = :id"),
                     {"k": crypto.wrap_family_key(fid), "id": fid})


def downgrade() -> None:
    op.drop_column("family", "wrapped_key")
