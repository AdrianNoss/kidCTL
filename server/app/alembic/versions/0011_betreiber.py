"""betreiber

Revision: 0011
Vorgänger: 0010
Erstellt: 2026-11-17 19:49:30.099303
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('operator',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('totp_secret', sa.String(length=64), nullable=False),
    sa.Column('totp_last_step', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('operator_session',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('operator_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['operator_id'], ['operator.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_operator_session_operator_id'), 'operator_session', ['operator_id'], unique=False)
    op.create_table('signup_code',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('note', sa.String(length=255), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('family_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['operator.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )


def downgrade() -> None:
    op.drop_table('signup_code')
    op.drop_index(op.f('ix_operator_session_operator_id'), table_name='operator_session')
    op.drop_table('operator_session')
    op.drop_table('operator')
