"""Sperrzeiten je Wochentag (verschlüsselte JSON-Liste); Fenster über Mitternacht brauchen keine Schemaänderung.

Bestehende Regeln erhalten NULL (= keine Sperrzeiten).

Revision: 0028
Vorgänger: 0027 (ferien_feiertage)
Erstellt: 2026-11-23
Gilt für Wochen- und Ferienvorlage (``weekday_rule``, ``vacation_rule``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('weekday_rule', 'vacation_rule'):
        op.add_column(table, sa.Column('blocks', sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ('vacation_rule', 'weekday_rule'):
        op.drop_column(table, 'blocks')
