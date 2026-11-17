"""Ergebnis der Integritätsprüfung verschlüsseln (enthält Benutzernamen und Pfade der Kinder; Probelauf mit echten Daten)

Revision: 0008
Vorgänger: 0007
Erstellt: 2026-11-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
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
    op.alter_column("device", "integrity_json", type_=sa.Text(), existing_nullable=False)
    for rid, fid, name in conn.execute(sa.text("SELECT id, family_id, integrity_json FROM device")).all():
        conn.execute(sa.text("UPDATE device SET integrity_json = :v WHERE id = :id"),
                     {"v": crypto.seal(name, "device.integrity_json", family_id=fid), "id": rid})


def downgrade() -> None:
    from app import crypto
    conn = op.get_bind()
    _load_keys(conn)
    for rid, fid, name in conn.execute(sa.text("SELECT id, family_id, integrity_json FROM device")).all():
        conn.execute(sa.text("UPDATE device SET integrity_json = :v WHERE id = :id"),
                     {"v": crypto.open_(name, "device.integrity_json", family_id=fid), "id": rid})
    op.alter_column("device", "integrity_json", type_=sa.Text(), existing_nullable=False)
