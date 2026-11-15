"""Familien (Mandanten): Tabellen family und membership, family_id an allen familienbezogenen Tabellen

Bestehende Daten gehören danach zu Familie 1; der älteste Account wird Inhaber, alle weiteren Elternteile.
Kindernamen sind nur noch innerhalb einer Familie eindeutig.

Revision: 0002
Vorgänger: 0001
Erstellt: 2026-11-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCOPED = ["child", "weekday_rule", "day_exception", "device", "device_user", "usage_record", "device_app",
          "child_game_app", "child_program", "child_custom_program", "blocked_event", "game_session",
          "child_temp_grant", "child_web_bundle", "child_domain", "child_blocklist", "app_usage", "pairing_code",
          "message"]


def upgrade() -> None:
    op.create_table(
        "family",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("family.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("parent.id", ondelete="CASCADE"), nullable=False,
                  unique=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="parent"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_membership_family_id", "membership", ["family_id"])

    conn = op.get_bind()
    has_data = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM parent) OR EXISTS (SELECT 1 FROM child) OR EXISTS (SELECT 1 FROM device)")).scalar()
    if has_data:
        conn.execute(sa.text("INSERT INTO family (id, name) VALUES (1, '')"))
        conn.execute(sa.text("SELECT setval(pg_get_serial_sequence('family', 'id'), 1)"))
        conn.execute(sa.text(
            "INSERT INTO membership (family_id, parent_id, role) "
            "SELECT 1, id, CASE WHEN id = (SELECT min(id) FROM parent) THEN 'owner' ELSE 'parent' END FROM parent"))

    for table in SCOPED:
        op.add_column(table, sa.Column("family_id", sa.Integer(), nullable=True))
        if has_data:
            conn.execute(sa.text(f'UPDATE "{table}" SET family_id = 1'))
        op.alter_column(table, "family_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_family_id", table, "family", ["family_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{table}_family_id", table, ["family_id"])

    op.drop_constraint("child_name_key", "child", type_="unique")
    op.create_unique_constraint("child_family_id_name_key", "child", ["family_id", "name"])
    op.alter_column("family", "name", server_default=None)
    op.alter_column("family", "created_at", server_default=None)
    op.alter_column("membership", "role", server_default=None)
    op.alter_column("membership", "created_at", server_default=None)


def downgrade() -> None:
    op.drop_constraint("child_family_id_name_key", "child", type_="unique")
    op.create_unique_constraint("child_name_key", "child", ["name"])
    for table in reversed(SCOPED):
        op.drop_index(f"ix_{table}_family_id", table_name=table)
        op.drop_constraint(f"fk_{table}_family_id", table, type_="foreignkey")
        op.drop_column(table, "family_id")
    op.drop_index("ix_membership_family_id", table_name="membership")
    op.drop_table("membership")
    op.drop_table("family")
