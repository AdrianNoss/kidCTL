"""E-Mail-Adresse als Anmeldung: systemweit eindeutig (ohne Groß/klein), Bestätigung per Link

Bestehende Adressen gelten als bestätigt (wurden schon für Passwort-Reset und Berichte genutzt). Gibt es dieselbe
Adresse mehrfach, bricht die Migration mit einer Liste ab – erst bereinigen.

Revision: 0003
Vorgänger: 0002
Erstellt: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dupes = conn.execute(sa.text(
        "SELECT lower(email), string_agg(username, ', ') FROM parent WHERE email <> '' "
        "GROUP BY lower(email) HAVING count(*) > 1")).all()
    if dupes:
        listing = "; ".join(f"{mail}: {users}" for mail, users in dupes)
        raise RuntimeError(f"E-Mail-Adressen mehrfach vergeben ({listing}) – bitte im Profil ändern, dann neu starten.")
    op.add_column("parent", sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    conn.execute(sa.text("UPDATE parent SET email_verified_at = now() WHERE email <> ''"))
    op.create_index("ix_parent_email_lower", "parent", [sa.text("lower(email)")], unique=True,
                    postgresql_where=sa.text("email <> ''"))


def downgrade() -> None:
    op.drop_index("ix_parent_email_lower", table_name="parent")
    op.drop_column("parent", "email_verified_at")
