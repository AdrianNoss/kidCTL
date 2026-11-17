"""Gerätename der Kopplungscodes verschlüsseln (war im Probelauf mit echten Daten noch Klartext)

Revision: 0007
Vorgänger: 0006
Erstellt: 2026-11-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
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
    op.alter_column("pairing_code", "device_name", type_=sa.Text(), existing_nullable=False)
    for rid, fid, name in conn.execute(sa.text("SELECT id, family_id, device_name FROM pairing_code")).all():
        conn.execute(sa.text("UPDATE pairing_code SET device_name = :v WHERE id = :id"),
                     {"v": crypto.seal(name, "pairing_code.device_name", family_id=fid), "id": rid})


def downgrade() -> None:
    from app import crypto
    conn = op.get_bind()
    _load_keys(conn)
    for rid, fid, name in conn.execute(sa.text("SELECT id, family_id, device_name FROM pairing_code")).all():
        conn.execute(sa.text("UPDATE pairing_code SET device_name = :v WHERE id = :id"),
                     {"v": crypto.open_(name, "pairing_code.device_name", family_id=fid), "id": rid})
    op.alter_column("pairing_code", "device_name", type_=sa.String(length=128), existing_nullable=False)
