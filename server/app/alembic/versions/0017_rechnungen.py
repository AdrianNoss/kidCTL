"""rechnungen

Revision: 0017
Vorgänger: 0016
Erstellt: 2026-11-18 18:06:27.534055
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('invoice',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=True),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('seq', sa.Integer(), nullable=True),
    sa.Column('issue_date', sa.Date(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('service_start', sa.Date(), nullable=True),
    sa.Column('service_end', sa.Date(), nullable=True),
    sa.Column('tax_mode', sa.String(length=16), nullable=False),
    sa.Column('tax_rate', sa.Integer(), nullable=False),
    sa.Column('net_cents', sa.Integer(), nullable=False),
    sa.Column('tax_cents', sa.Integer(), nullable=False),
    sa.Column('gross_cents', sa.Integer(), nullable=False),
    sa.Column('family_label', sa.Text(), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('sender', sa.Text(), nullable=False),
    sa.Column('pdf', sa.Text(), nullable=False),
    sa.Column('storno_of', sa.Integer(), nullable=True),
    sa.Column('reference', sa.String(length=64), nullable=False),
    sa.Column('paid_on', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('issued_at', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['storno_of'], ['invoice.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_invoice_family_id'), 'invoice', ['family_id'], unique=False)
    op.create_index(op.f('ix_invoice_status'), 'invoice', ['status'], unique=False)
    op.create_table('invoice_counter',
    sa.Column('year', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('last', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('year')
    )
    op.create_table('invoice_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sender_name', sa.String(length=128), nullable=False),
    sa.Column('sender_street', sa.String(length=128), nullable=False),
    sa.Column('sender_postal_code', sa.String(length=16), nullable=False),
    sa.Column('sender_city', sa.String(length=64), nullable=False),
    sa.Column('sender_country', sa.String(length=64), nullable=False),
    sa.Column('tax_id', sa.String(length=64), nullable=False),
    sa.Column('contact', sa.String(length=255), nullable=False),
    sa.Column('bank_holder', sa.String(length=128), nullable=False),
    sa.Column('iban', sa.String(length=64), nullable=False),
    sa.Column('bic', sa.String(length=16), nullable=False),
    sa.Column('footer', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('price_item',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('amount_cents', sa.Integer(), nullable=False),
    sa.Column('months', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('invoice_line',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('invoice_id', sa.Integer(), nullable=False),
    sa.Column('pos', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(length=255), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_cents', sa.Integer(), nullable=False),
    sa.Column('total_cents', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoice.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_invoice_line_invoice_id'), 'invoice_line', ['invoice_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_invoice_line_invoice_id'), table_name='invoice_line')
    op.drop_table('invoice_line')
    op.drop_table('price_item')
    op.drop_table('invoice_settings')
    op.drop_table('invoice_counter')
    op.drop_index(op.f('ix_invoice_status'), table_name='invoice')
    op.drop_index(op.f('ix_invoice_family_id'), table_name='invoice')
    op.drop_table('invoice')
