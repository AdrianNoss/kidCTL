"""„Zählt nicht“-Programme: Tabelle ``child_app_rule`` (Einstellungen je Kind und App, vorerst ``free``).

Neue Tabelle, bestehende Daten bleiben unberührt (keine Zeile = zählt wie bisher).

Revision: 0029
Vorgänger: 0028 (sperrzeiten)
Erstellt: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('child_app_rule',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('child_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=512), nullable=False),
    sa.Column('free', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['child_id'], ['child.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('child_id', 'key')
    )
    op.create_index(op.f('ix_child_app_rule_child_id'), 'child_app_rule', ['child_id'], unique=False)
    op.create_index(op.f('ix_child_app_rule_family_id'), 'child_app_rule', ['family_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_child_app_rule_family_id'), table_name='child_app_rule')
    op.drop_index(op.f('ix_child_app_rule_child_id'), table_name='child_app_rule')
    op.drop_table('child_app_rule')
