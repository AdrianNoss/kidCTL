"""Nintendo: alte „Stand“-Werte leeren

Bis 0032 stand in ``nintendo_console.last_sync`` der Einstellungs-Sync der Switch (Bibliothek), nicht die letzte neue
Spielzeit. Die alten Werte sind falsch und würden stehen bleiben, bis sich die Spielzeit ändert; deshalb leeren.

Revision: 0033
Vorgänger: 0032 (nintendo)
Erstellt: 2026-11-27
"""

from __future__ import annotations

from alembic import op


revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE nintendo_console SET last_sync = NULL")


def downgrade() -> None:
    pass   # Datenbereinigung, nichts zurückzuholen
