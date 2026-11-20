"""Homepage-Link (Branding) und Einladungsmail (signup_code)

Revision: 0022
Vorgänger: 0021
Erstellt: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None

BRANDING = (("homepage_url", 255), ("homepage_label_de", 64), ("homepage_label_en", 64))


def upgrade() -> None:
    for name, n in BRANDING:
        op.add_column('branding', sa.Column(name, sa.String(length=n), server_default='', nullable=False))
    op.add_column('signup_code', sa.Column('email', sa.Text(), nullable=True))
    op.add_column('signup_code', sa.Column('lang', sa.String(length=2), server_default='de', nullable=False))


def downgrade() -> None:
    op.drop_column('signup_code', 'lang')
    op.drop_column('signup_code', 'email')
    for name, _n in reversed(BRANDING):
        op.drop_column('branding', name)
