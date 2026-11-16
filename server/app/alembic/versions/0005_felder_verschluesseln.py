"""Sensible Felder je Familie verschlüsseln (Sealed = AES-GCM, Matchable = AES-SIV)

Stellt die Spalten auf TEXT um und verschlüsselt alle vorhandenen Werte mit dem Schlüssel der Familie der Zeile.
Braucht KIDCTL_MASTER_KEY. Nicht umkehrbar ohne Schlüssel (downgrade entschlüsselt wieder).

Revision: 0005
Vorgänger: 0004
Erstellt: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# (Tabelle, Spalte, deterministisch?, alte Länge oder None für TEXT)
FIELDS = [
    ("child", "name", True, 64),
    ("device", "name", False, 128), ("device", "hostname", False, 255),
    ("device_user", "username", True, 64), ("device_user", "full_name", False, 255),
    ("usage_record", "username", True, 64),
    ("blocked_event", "username", True, 64), ("blocked_event", "key", True, 512), ("blocked_event", "name", False, 255),
    ("blocked_event", "detail", False, 255), ("blocked_event", "detail_arg", False, 255),
    ("game_session", "username", True, 64), ("game_session", "key", True, 512), ("game_session", "name", False, 255),
    ("app_usage", "username", True, 64), ("app_usage", "key", True, 512), ("app_usage", "name", False, 255),
    ("message", "body", False, None), ("message", "access_key", True, 512), ("message", "access_name", False, 255),
]


def _load_keys(conn) -> None:
    from app import crypto
    for fid, wrapped in conn.execute(sa.text("SELECT id, wrapped_key FROM family WHERE wrapped_key IS NOT NULL")):
        crypto.remember(fid, crypto.unwrap_family_key(fid, bytes(wrapped)))


def _convert(encrypt: bool) -> None:
    from app import crypto
    conn = op.get_bind()
    _load_keys(conn)
    for table, col, det, length in FIELDS:
        if encrypt:
            op.alter_column(table, col, type_=sa.Text(), existing_nullable=True)
        label = f"{table}.{col}"
        rows = conn.execute(sa.text(f'SELECT id, family_id, "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL')).all()
        for rid, fid, value in rows:
            if encrypt:
                new = (crypto.match if det else crypto.seal)(value, label, family_id=fid)
            else:
                new = crypto.open_(value, label, family_id=fid)
            conn.execute(sa.text(f'UPDATE "{table}" SET "{col}" = :v WHERE id = :id'), {"v": new, "id": rid})
        if not encrypt and length:
            op.alter_column(table, col, type_=sa.String(length), existing_nullable=True)


def upgrade() -> None:
    _convert(encrypt=True)


def downgrade() -> None:
    _convert(encrypt=False)
