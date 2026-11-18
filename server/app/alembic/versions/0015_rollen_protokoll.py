"""rollen_protokoll

Revision: 0015
Vorgänger: 0014
Erstellt: 2026-11-18 17:28:38.078056
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('admin_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('operator_id', sa.Integer(), nullable=True),
    sa.Column('family_id', sa.Integer(), nullable=True),
    sa.Column('object', sa.String(length=32), nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=True),
    sa.Column('field', sa.String(length=64), nullable=False),
    sa.Column('old', sa.Text(), nullable=False),
    sa.Column('new', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['operator_id'], ['operator.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_admin_log_at'), 'admin_log', ['at'], unique=False)
    op.create_index(op.f('ix_admin_log_family_id'), 'admin_log', ['family_id'], unique=False)
    op.add_column('operator', sa.Column('role', sa.String(length=16), server_default='admin', nullable=False))
    op.add_column('operator', sa.Column('email', sa.String(length=255), server_default='', nullable=False))
    op.add_column('operator', sa.Column('ticket_mails', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    op.drop_column('operator', 'ticket_mails')
    op.drop_column('operator', 'email')
    op.drop_column('operator', 'role')
    op.drop_index(op.f('ix_admin_log_family_id'), table_name='admin_log')
    op.drop_index(op.f('ix_admin_log_at'), table_name='admin_log')
    op.drop_table('admin_log')
