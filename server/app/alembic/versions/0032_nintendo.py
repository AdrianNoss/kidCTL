"""Nintendo-Switch-Spielzeit (experimentell): Verknüpfung, Konsolen, Spielerprofile, Spielzeit je Tag

Neue Tabellen, keine bestehenden Zeilen betroffen.

Revision: 0032
Vorgänger: 0031 (app_limits)
Erstellt: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('nintendo_console',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_key', sa.Text(), nullable=False),   # Matchable (verschlüsselt)
    sa.Column('name', sa.Text(), nullable=False),   # Sealed (verschlüsselt)
    sa.Column('model', sa.String(length=16), nullable=False),
    sa.Column('day', sa.Date(), nullable=True),
    sa.Column('used_minutes', sa.Integer(), nullable=True),
    sa.Column('limit_minutes', sa.Integer(), nullable=True),
    sa.Column('remaining_minutes', sa.Integer(), nullable=True),
    sa.Column('last_sync', sa.DateTime(), nullable=True),
    sa.Column('stale', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('family_id', 'device_key')
    )
    op.create_index(op.f('ix_nintendo_console_family_id'), 'nintendo_console', ['family_id'], unique=False)
    op.create_table('nintendo_link',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_token', sa.Text(), nullable=True),   # Sealed (verschlüsselt)
    sa.Column('pending_verifier', sa.Text(), nullable=True),   # Sealed (verschlüsselt)
    sa.Column('pending_url', sa.Text(), nullable=True),   # Sealed (verschlüsselt)
    sa.Column('pending_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=32), nullable=False),
    sa.Column('failures', sa.Integer(), nullable=False),
    sa.Column('next_poll_at', sa.DateTime(), nullable=True),
    sa.Column('last_ok_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('family_id')
    )
    op.create_index(op.f('ix_nintendo_link_family_id'), 'nintendo_link', ['family_id'], unique=False)
    op.create_index(op.f('ix_nintendo_link_next_poll_at'), 'nintendo_link', ['next_poll_at'], unique=False)
    op.create_table('nintendo_player',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('console_id', sa.Integer(), nullable=False),
    sa.Column('player_key', sa.Text(), nullable=False),   # Matchable (verschlüsselt)
    sa.Column('nickname', sa.Text(), nullable=False),   # Sealed (verschlüsselt)
    sa.Column('child_id', sa.Integer(), nullable=True),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['child_id'], ['child.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['console_id'], ['nintendo_console.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('console_id', 'player_key')
    )
    op.create_index(op.f('ix_nintendo_player_console_id'), 'nintendo_player', ['console_id'], unique=False)
    op.create_index(op.f('ix_nintendo_player_family_id'), 'nintendo_player', ['family_id'], unique=False)
    op.create_table('nintendo_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('child_id', sa.Integer(), nullable=True),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('minutes', sa.Integer(), nullable=False),
    sa.Column('apps', sa.Text(), nullable=False),   # Sealed (verschlüsselt)
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['child_id'], ['child.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['player_id'], ['nintendo_player.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('player_id', 'day')
    )
    op.create_index(op.f('ix_nintendo_usage_child_id'), 'nintendo_usage', ['child_id'], unique=False)
    op.create_index(op.f('ix_nintendo_usage_day'), 'nintendo_usage', ['day'], unique=False)
    op.create_index(op.f('ix_nintendo_usage_family_id'), 'nintendo_usage', ['family_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_nintendo_usage_family_id'), table_name='nintendo_usage')
    op.drop_index(op.f('ix_nintendo_usage_day'), table_name='nintendo_usage')
    op.drop_index(op.f('ix_nintendo_usage_child_id'), table_name='nintendo_usage')
    op.drop_table('nintendo_usage')
    op.drop_index(op.f('ix_nintendo_player_family_id'), table_name='nintendo_player')
    op.drop_index(op.f('ix_nintendo_player_console_id'), table_name='nintendo_player')
    op.drop_table('nintendo_player')
    op.drop_index(op.f('ix_nintendo_link_next_poll_at'), table_name='nintendo_link')
    op.drop_index(op.f('ix_nintendo_link_family_id'), table_name='nintendo_link')
    op.drop_table('nintendo_link')
    op.drop_index(op.f('ix_nintendo_console_family_id'), table_name='nintendo_console')
    op.drop_table('nintendo_console')
