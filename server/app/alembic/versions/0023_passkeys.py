"""Passkeys, WebAuthn-Challenges, frische Bestätigung an Sitzungen

Revision: 0023
Vorgänger: 0022
Erstellt: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('passkey',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('credential_id', sa.String(length=512), nullable=False),
    sa.Column('public_key', sa.LargeBinary(), nullable=False),
    sa.Column('sign_count', sa.Integer(), nullable=False),
    sa.Column('transports', sa.String(length=128), nullable=False),
    sa.Column('backed_up', sa.Boolean(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('credential_id')
    )
    op.create_index(op.f('ix_passkey_account_id'), 'passkey', ['account_id'], unique=False)
    op.create_table('webauthn_challenge',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('challenge', sa.LargeBinary(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_webauthn_challenge_expires_at'), 'webauthn_challenge', ['expires_at'], unique=False)
    op.add_column('parent_session', sa.Column('confirmed_at', sa.DateTime(), nullable=True))
    op.add_column('operator_session', sa.Column('confirmed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('operator_session', 'confirmed_at')
    op.drop_column('parent_session', 'confirmed_at')
    op.drop_index(op.f('ix_webauthn_challenge_expires_at'), table_name='webauthn_challenge')
    op.drop_table('webauthn_challenge')
    op.drop_index(op.f('ix_passkey_account_id'), table_name='passkey')
    op.drop_table('passkey')
