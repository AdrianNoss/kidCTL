"""login_ticket (Anmeldung in zwei Schritten)

Revision: 0021
Vorgänger: 0020
Erstellt: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('login_ticket',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_login_ticket_expires_at'), 'login_ticket', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_login_ticket_expires_at'), table_name='login_ticket')
    op.drop_table('login_ticket')
