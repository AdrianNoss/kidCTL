"""Zeitkonto je Kind: Schalter, Anteil, Deckel je Tag, Verfall und Starttag

Bestehende Kinder: ausgeschaltet, Standardwerte 50 % / 30 min / 7 Tage, kein Starttag.

Revision: 0030
Vorgänger: 0029 (zaehlt_nicht)
Erstellt: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None

COLUMNS = (('bank_enabled', sa.Boolean(), sa.false()), ('bank_percent', sa.Integer(), '50'),
           ('bank_cap_minutes', sa.Integer(), '30'), ('bank_expiry_days', sa.Integer(), '7'))


def upgrade() -> None:
    for name, type_, default in COLUMNS:
        op.add_column('child', sa.Column(name, type_, nullable=False, server_default=default))
        op.alter_column('child', name, server_default=None)
    op.add_column('child', sa.Column('bank_since', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('child', 'bank_since')
    for name, _type, _default in reversed(COLUMNS):
        op.drop_column('child', name)
