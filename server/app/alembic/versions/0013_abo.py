"""abo

Revision: 0013
Vorgänger: 0012
Erstellt: 2026-11-17 20:45:28.250193
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('family', sa.Column('plan', sa.String(length=16), server_default='free', nullable=False))
    op.add_column('family', sa.Column('billing_status', sa.String(length=16), server_default='active', nullable=False))
    op.add_column('family', sa.Column('trial_ends_at', sa.DateTime(), nullable=True))
    op.add_column('family', sa.Column('paid_until', sa.DateTime(), nullable=True))
    op.add_column('family', sa.Column('billing_provider', sa.String(length=32), server_default='', nullable=False))
    op.add_column('family', sa.Column('billing_customer', sa.String(length=128), server_default='', nullable=False))
    op.add_column('family', sa.Column('billing_notified', sa.String(length=255), server_default='', nullable=False))
    op.add_column('signup_code', sa.Column('plan', sa.String(length=16), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('signup_code', 'plan')
    op.drop_column('family', 'billing_notified')
    op.drop_column('family', 'billing_customer')
    op.drop_column('family', 'billing_provider')
    op.drop_column('family', 'paid_until')
    op.drop_column('family', 'trial_ends_at')
    op.drop_column('family', 'billing_status')
    op.drop_column('family', 'plan')
