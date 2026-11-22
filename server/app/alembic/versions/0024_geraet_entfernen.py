"""Gerät entfernen: Nutzungsstatistik bleibt erhalten (device_id → SET NULL)

Revision: 0024
Vorgänger: 0023
Erstellt: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

TABLES = ("usage_record", "app_usage")


def upgrade() -> None:
    for t in TABLES:
        op.alter_column(t, 'device_id', existing_type=sa.INTEGER(), nullable=True)
        op.drop_constraint(f'{t}_device_id_fkey', t, type_='foreignkey')
        op.create_foreign_key(f'{t}_device_id_fkey', t, 'device', ['device_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DELETE FROM {t} WHERE device_id IS NULL")
        op.drop_constraint(f'{t}_device_id_fkey', t, type_='foreignkey')
        op.create_foreign_key(f'{t}_device_id_fkey', t, 'device', ['device_id'], ['id'], ondelete='CASCADE')
        op.alter_column(t, 'device_id', existing_type=sa.INTEGER(), nullable=False)
