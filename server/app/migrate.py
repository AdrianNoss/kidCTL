"""Leichtgewichtige Schema-Migrationen.

Muster: Spalte/Tabelle per ``PRAGMA table_info`` prüfen -> ``ALTER TABLE`` -> nach dem Commit
verifizieren -> bei Fehlschlag ``RuntimeError``. Wird in ``init_db()`` vor ``create_all()``
aufgerufen; ``create_all()`` legt nur fehlende Tabellen an, ändert bestehende aber nicht.

Neue Spalten in ``COLUMN_MIGRATIONS`` eintragen: (Tabelle, Spalte, DDL-Typ inkl. Default).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, text

log = logging.getLogger("kidctl.migrate")

# (Tabelle, Spalte, DDL), z. B. ("device", "alert_hours", "INTEGER NOT NULL DEFAULT 48")
COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # 2026-10-13: Chat/Verlängerungen
    ("message", "approved_minutes", "INTEGER"),
    ("message", "approved_window_end", "TIME"),
    ("message", "device_id", "INTEGER REFERENCES device(id) ON DELETE SET NULL"),
    ("message", "client_msg_id", "VARCHAR(64)"),
]


def _columns(conn, table: str) -> list[str]:
    return [row[1] for row in conn.execute(text(f'PRAGMA table_info("{table}")'))]


def ensure_column(engine: Engine, table: str, column: str, ddl: str) -> bool:
    """Fügt ``column`` zu ``table`` hinzu, falls sie fehlt. Gibt True zurück, wenn migriert wurde."""
    with engine.connect() as conn:
        before = _columns(conn, table)
    if not before:
        log.info("migrate: Tabelle %s existiert noch nicht – create_all() legt sie an", table)
        return False
    if column in before:
        return False

    log.warning("migrate: DB=%s Tabelle=%s Spalten vorher=%s", engine.url.database, table, before)
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))
    with engine.connect() as conn:
        after = _columns(conn, table)
    log.warning("migrate: DB=%s Tabelle=%s Spalten nachher=%s", engine.url.database, table, after)
    if column not in after:
        raise RuntimeError(f"Migration fehlgeschlagen: {table}.{column} fehlt nach ALTER TABLE")
    return True


def run_migrations(engine: Engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        raise RuntimeError("migrate.py unterstützt nur SQLite")
    for table, column, ddl in COLUMN_MIGRATIONS:
        ensure_column(engine, table, column, ddl)
