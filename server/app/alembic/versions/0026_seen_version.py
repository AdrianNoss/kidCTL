"""Hinweis „Neu in Version …“: zuletzt bestätigte Server-Version je Elternteil

Bestehende Eltern starten bei 0.45.2 (Version beim Einführen), sehen also ab dem nächsten Update den Hinweis.

Revision: 0026
Vorgänger: 0024 (beim Zusammenführen mit 0025_update_knopf auf '0025' umstellen)
Erstellt: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('parent', sa.Column('seen_version', sa.String(length=32), nullable=False,
                                      server_default='0.45.2'))
    op.alter_column('parent', 'seen_version', server_default=None)


def downgrade() -> None:
    op.drop_column('parent', 'seen_version')
