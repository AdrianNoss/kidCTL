"""E-Mail-Adresse verschlüsseln (Schlüssel der Familie des Accounts), Suchwert email_index für Anmeldung/Eindeutigkeit

Revision: 0006
Vorgänger: 0005
Erstellt: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _load_keys(conn) -> None:
    from app import crypto
    for fid, wrapped in conn.execute(sa.text("SELECT id, wrapped_key FROM family WHERE wrapped_key IS NOT NULL")):
        crypto.remember(fid, crypto.unwrap_family_key(fid, bytes(wrapped)))


def upgrade() -> None:
    from app import crypto
    conn = op.get_bind()
    _load_keys(conn)
    op.add_column("parent", sa.Column("email_index", sa.String(length=64), nullable=False, server_default=""))
    op.alter_column("parent", "email", type_=sa.Text(), existing_nullable=False)
    rows = conn.execute(sa.text("SELECT p.id, p.email, m.family_id FROM parent p JOIN membership m ON m.parent_id = p.id"
                                " WHERE p.email IS NOT NULL")).all()
    for pid, email, fid in rows:
        conn.execute(sa.text("UPDATE parent SET email = :e, email_index = :i WHERE id = :id"),
                     {"e": crypto.seal(email, "parent.email", family_id=fid), "i": crypto.email_index(email), "id": pid})
    op.drop_index("ix_parent_email_lower", table_name="parent")
    op.create_index("ix_parent_email_index", "parent", ["email_index"], unique=True,
                    postgresql_where=sa.text("email_index <> ''"))
    op.alter_column("parent", "email_index", server_default=None)


def downgrade() -> None:
    from app import crypto
    conn = op.get_bind()
    _load_keys(conn)
    rows = conn.execute(sa.text("SELECT p.id, p.email, m.family_id FROM parent p JOIN membership m ON m.parent_id = p.id"
                                " WHERE p.email IS NOT NULL")).all()
    for pid, email, fid in rows:
        conn.execute(sa.text("UPDATE parent SET email = :e WHERE id = :id"),
                     {"e": crypto.open_(email, "parent.email", family_id=fid), "id": pid})
    op.drop_index("ix_parent_email_index", table_name="parent")
    op.alter_column("parent", "email", type_=sa.String(length=255), existing_nullable=False)
    op.create_index("ix_parent_email_lower", "parent", [sa.text("lower(email)")], unique=True,
                    postgresql_where=sa.text("email <> ''"))
    op.drop_column("parent", "email_index")
