"""Sperrzeiten je Wochentag (verschlüsselte JSON-Liste); Fenster über Mitternacht brauchen keine Schemaänderung.

Bestehende Regeln erhalten NULL (= keine Sperrzeiten).

Revision: 0027
Vorgänger: 0026 (seen_version)
Erstellt: 2026-11-23
Hinweis: K2 (Ferien/Feiertage) belegt ebenfalls 0027 – beim Zusammenführen diese Revision hinter K2 hängen und
die Spalte auch für ``vacation_rule`` anlegen.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('weekday_rule', sa.Column('blocks', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('weekday_rule', 'blocks')
