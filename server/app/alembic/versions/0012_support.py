"""support

Revision: 0012
Vorgänger: 0011
Erstellt: 2026-11-17 20:21:06.743700
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('support_access',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('operator', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_support_access_at'), 'support_access', ['at'], unique=False)
    op.create_index(op.f('ix_support_access_family_id'), 'support_access', ['family_id'], unique=False)
    op.create_table('support_grant',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('granted_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['granted_by'], ['parent.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_support_grant_family_id'), 'support_grant', ['family_id'], unique=False)
    op.create_table('support_session',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('operator_session_id', sa.Integer(), nullable=False),
    sa.Column('grant_id', sa.Integer(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['grant_id'], ['support_grant.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['operator_session_id'], ['operator_session.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )


def downgrade() -> None:
    op.drop_table('support_session')
    op.drop_index(op.f('ix_support_grant_family_id'), table_name='support_grant')
    op.drop_table('support_grant')
    op.drop_index(op.f('ix_support_access_family_id'), table_name='support_access')
    op.drop_index(op.f('ix_support_access_at'), table_name='support_access')
    op.drop_table('support_access')
