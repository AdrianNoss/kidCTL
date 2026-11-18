"""tickets

Revision: 0016
Vorgänger: 0015
Erstellt: 2026-11-18 17:51:28.558693
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ticket',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('meta', sa.Text(), nullable=False),
    sa.Column('last_operator', sa.String(length=64), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['parent.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ticket_family_id'), 'ticket', ['family_id'], unique=False)
    op.create_index(op.f('ix_ticket_status'), 'ticket', ['status'], unique=False)
    op.create_table('ticket_message',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('from_support', sa.Boolean(), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('author', sa.Text(), nullable=False),
    sa.Column('internal', sa.Boolean(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['parent.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ticket_id'], ['ticket.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ticket_message_family_id'), 'ticket_message', ['family_id'], unique=False)
    op.create_index(op.f('ix_ticket_message_ticket_id'), 'ticket_message', ['ticket_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ticket_message_ticket_id'), table_name='ticket_message')
    op.drop_index(op.f('ix_ticket_message_family_id'), table_name='ticket_message')
    op.drop_table('ticket_message')
    op.drop_index(op.f('ix_ticket_status'), table_name='ticket')
    op.drop_index(op.f('ix_ticket_family_id'), table_name='ticket')
    op.drop_table('ticket')
