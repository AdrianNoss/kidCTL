"""newsletter (Opt-in je Familie, Archiv)

Revision: 0019
Vorgänger: 0018
Erstellt: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('newsletter',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('subject_de', sa.String(length=200), nullable=False),
    sa.Column('body_de', sa.Text(), nullable=False),
    sa.Column('subject_en', sa.String(length=200), nullable=False),
    sa.Column('body_en', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('sent_by', sa.String(length=64), nullable=False),
    sa.Column('families', sa.Integer(), nullable=False),
    sa.Column('recipients', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.add_column('family', sa.Column('newsletter_at', sa.DateTime(), nullable=True))   # bestehende: kein Opt-in


def downgrade() -> None:
    op.drop_column('family', 'newsletter_at')
    op.drop_table('newsletter')
