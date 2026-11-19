"""anbieter (Fußzeile der Mails)

Revision: 0020
Vorgänger: 0019
Erstellt: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None

FIELDS = (("provider_name", 128), ("provider_address", 255), ("provider_email", 255), ("imprint_url", 255),
          ("privacy_url", 255), ("news_url", 255))


def upgrade() -> None:
    for name, n in FIELDS:
        op.add_column('branding', sa.Column(name, sa.String(length=n), server_default='', nullable=False))


def downgrade() -> None:
    for name, _n in reversed(FIELDS):
        op.drop_column('branding', name)
