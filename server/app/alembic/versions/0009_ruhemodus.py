"""Ruhemodus

Revision: 0009
Vorgänger: 0008
Erstellt: 2026-11-17 18:30:25.819586
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('family', sa.Column('paused_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('family', 'paused_at')
