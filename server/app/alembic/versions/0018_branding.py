"""branding (Whitelabel)

Revision: 0018
Vorgänger: 0017
Erstellt: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('branding',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), server_default='', nullable=False),
    sa.Column('logo_only', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('logo', sa.LargeBinary(), nullable=True),
    sa.Column('icon', sa.LargeBinary(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('branding')
