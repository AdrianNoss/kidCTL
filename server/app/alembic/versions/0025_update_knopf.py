"""Update auf Knopfdruck: Fähigkeiten des Agenten und Stand des angestoßenen Updates je Gerät

Revision: 0025
Vorgänger: 0024
Erstellt: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('device', sa.Column('features', sa.String(length=255), server_default='', nullable=False))
    op.add_column('device', sa.Column('update_requested_at', sa.DateTime(), nullable=True))
    op.add_column('device', sa.Column('update_from', sa.String(length=64), server_default='', nullable=False))
    op.add_column('device', sa.Column('update_state', sa.String(length=16), server_default='', nullable=False))
    op.add_column('device', sa.Column('update_detail', sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ('update_detail', 'update_state', 'update_from', 'update_requested_at', 'features'):
        op.drop_column('device', col)
