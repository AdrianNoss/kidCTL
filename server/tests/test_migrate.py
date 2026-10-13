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
