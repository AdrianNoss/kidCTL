"""rechnungsangaben

Revision: 0014
Vorgänger: 0013
Erstellt: 2026-11-17 21:03:35.465762
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('billing_address',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('street', sa.Text(), nullable=False),
    sa.Column('postal_code', sa.Text(), nullable=False),
    sa.Column('city', sa.Text(), nullable=False),
    sa.Column('country', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('vat_id', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['family_id'], ['family.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('family_id')
    )
    op.create_index(op.f('ix_billing_address_family_id'), 'billing_address', ['family_id'], unique=False)
    op.alter_column('family', 'name',
               existing_type=sa.VARCHAR(length=128),
               type_=sa.Text(),
               existing_nullable=False)
    # Bestehende Familiennamen mit dem Plattform-Schlüssel verschlüsseln (leere bleiben leer)
    from app.crypto import PLATFORM, seal_platform
    conn = op.get_bind()
    for fid, name in conn.execute(sa.text("SELECT id, name FROM family")).all():
        if name and not name.startswith(PLATFORM):
            conn.execute(sa.text("UPDATE family SET name = :n WHERE id = :id"),
                         {"n": seal_platform(name, "family.name"), "id": fid})


def downgrade() -> None:
    from app.crypto import open_platform
    conn = op.get_bind()
    for fid, name in conn.execute(sa.text("SELECT id, name FROM family")).all():
        conn.execute(sa.text("UPDATE family SET name = :n WHERE id = :id"),
                     {"n": open_platform(name or "", "family.name")[:128], "id": fid})
    op.alter_column('family', 'name',
               existing_type=sa.Text(),
               type_=sa.VARCHAR(length=128),
               existing_nullable=False)
    op.drop_index(op.f('ix_billing_address_family_id'), table_name='billing_address')
    op.drop_table('billing_address')
