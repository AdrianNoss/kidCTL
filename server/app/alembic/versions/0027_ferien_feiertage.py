"""Ferien und Feiertage: Ferienzeiträume und Feiertagsregion je Familie, Ferienvorlage je Kind

Nur neue Tabellen; bestehende Familien haben keine Ferien/Feiertage, ihre Policies bleiben unverändert.

Revision: 0027
Vorgänger: 0026 (seen_version)
Erstellt: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('holiday_region',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('subdiv', sa.String(length=16), nullable=False),
    sa.Column('as_weekday', sa.Integer(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('family_id')
    )
    op.create_index(op.f('ix_holiday_region_family_id'), 'holiday_region', ['family_id'], unique=False)
    op.create_table('vacation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),   # Sealed (verschlüsselt)
    sa.Column('first_day', sa.Date(), nullable=False),
    sa.Column('last_day', sa.Date(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vacation_family_id'), 'vacation', ['family_id'], unique=False)
    op.create_table('vacation_rule',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('child_id', sa.Integer(), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('quota_minutes', sa.Integer(), nullable=False),
    sa.Column('window_start', sa.Time(), nullable=False),
    sa.Column('window_end', sa.Time(), nullable=False),
    sa.Column('game_minutes', sa.Integer(), nullable=True),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['child_id'], ['child.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('child_id', 'weekday')
    )
    op.create_index(op.f('ix_vacation_rule_family_id'), 'vacation_rule', ['family_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_vacation_rule_family_id'), table_name='vacation_rule')
    op.drop_table('vacation_rule')
    op.drop_index(op.f('ix_vacation_family_id'), table_name='vacation')
    op.drop_table('vacation')
    op.drop_index(op.f('ix_holiday_region_family_id'), table_name='holiday_region')
    op.drop_table('holiday_region')
