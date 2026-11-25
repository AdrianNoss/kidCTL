"""Limit je Programm/Kategorie: ``child_app_rule.limit_minutes`` und ``usage_record.limit_seconds``.

Bestehende Zeilen: kein Limit (NULL), keine Zeit je Limit (NULL).

Revision: 0031
Vorgänger: 0030 (zeitkonto)
Erstellt: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('child_app_rule', sa.Column('limit_minutes', sa.Integer(), nullable=True))
    op.add_column('usage_record', sa.Column('limit_seconds', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('usage_record', 'limit_seconds')
    op.drop_column('child_app_rule', 'limit_minutes')
