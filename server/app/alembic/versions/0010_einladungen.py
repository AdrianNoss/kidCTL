"""Einladungen

Revision: 0010
Vorgänger: 0009
Erstellt: 2026-11-17 19:40:25.178007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('family_invite',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['parent.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_family_invite_family_id'), 'family_invite', ['family_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_family_invite_family_id'), table_name='family_invite')
    op.drop_table('family_invite')
