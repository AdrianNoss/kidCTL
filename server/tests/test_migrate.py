"""Migrationshelfer gegen eine synthetische Alt-Schema-Datenbank."""

import pytest
from sqlalchemy import create_engine, text

from app import migrate


def test_ensure_column_on_old_schema(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
        c.execute(text("INSERT INTO device (name) VALUES ('alt')"))

    assert migrate.ensure_column(eng, "device", "alert_after_hours", "INTEGER NOT NULL DEFAULT 48")
    assert not migrate.ensure_column(eng, "device", "alert_after_hours", "INTEGER NOT NULL DEFAULT 48")
    with eng.connect() as c:
        assert c.execute(text("SELECT alert_after_hours FROM device")).scalar() == 48
    # Fehlende Tabelle -> kein Fehler, create_all() übernimmt
    assert not migrate.ensure_column(eng, "gibtsnicht", "x", "INTEGER")


def test_ensure_column_fails_hard(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY)"))
    calls = iter([["id"], ["id"]])  # Spalte "fehlt" auch nach ALTER TABLE
    monkeypatch.setattr(migrate, "_columns", lambda conn, table: next(calls))
    with pytest.raises(RuntimeError):
        migrate.ensure_column(eng, "device", "x", "INTEGER")


def test_init_db_on_existing_db_is_idempotent(client):
    from app.db import init_db
    init_db()
    init_db()


def test_message_columns_migrated_on_old_schema(tmp_path):
    """Alt-Schema der message-Tabelle (vor Chat-Erweiterung) bekommt die neuen Spalten."""
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY)"))
        c.execute(text(
            "CREATE TABLE message (id INTEGER PRIMARY KEY, child_id INTEGER, from_child BOOLEAN, "
            "kind VARCHAR(32), body TEXT, status VARCHAR(16))"))
        c.execute(text("INSERT INTO message (child_id, from_child, kind, body) VALUES (1, 1, 'text', 'alt')"))
    migrate.run_migrations(eng)
    with eng.connect() as c:
        cols = migrate._columns(c, "message")
        assert {"approved_minutes", "approved_window_end", "device_id", "client_msg_id"} <= set(cols)
        assert c.execute(text("SELECT body FROM message")).scalar() == "alt"
    migrate.run_migrations(eng)  # idempotent


def test_device_columns_migrated_on_old_schema(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
        c.execute(text("INSERT INTO device (name) VALUES ('alt')"))
    migrate.run_migrations(eng)
    with eng.connect() as c:
        assert {"policy_version", "offline_alerted_at"} <= set(migrate._columns(c, "device"))
        assert c.execute(text("SELECT policy_version FROM device")).scalar() == ""


def test_child_program_mode_migrated(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, name VARCHAR(64))"))
        c.execute(text("INSERT INTO child (name) VALUES ('Max')"))
    migrate.run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT program_mode FROM child")).scalar() == "off"
