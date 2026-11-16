"""Migrationshelfer gegen eine synthetische Alt-Schema-Datenbank."""

import pytest
from sqlalchemy import create_engine, text

from app import sqlite_import as migrate


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
    migrate.upgrade_legacy(eng)
    with eng.connect() as c:
        cols = migrate._columns(c, "message")
        assert {"approved_minutes", "approved_window_end", "device_id", "client_msg_id"} <= set(cols)
        assert c.execute(text("SELECT body FROM message")).scalar() == "alt"
    migrate.upgrade_legacy(eng)  # idempotent


def test_device_columns_migrated_on_old_schema(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
        c.execute(text("INSERT INTO device (name) VALUES ('alt')"))
    migrate.upgrade_legacy(eng)
    with eng.connect() as c:
        assert {"policy_version", "offline_alerted_at"} <= set(migrate._columns(c, "device"))
        assert c.execute(text("SELECT policy_version FROM device")).scalar() == ""


def test_child_program_mode_migrated(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, name VARCHAR(64))"))
        c.execute(text("INSERT INTO child (name) VALUES ('Max')"))
    migrate.upgrade_legacy(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT program_mode FROM child")).scalar() == "off"


def test_child_web_columns_migrated(tmp_path):
    """Bestehende Kinder bekommen web_mode='off' (nicht die alte Vorgabe 'whitelist')."""
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, name VARCHAR(64), "
                       "web_filter_mode VARCHAR(16) DEFAULT 'whitelist')"))
        c.execute(text("INSERT INTO child (name, web_filter_mode) VALUES ('Max', 'whitelist')"))
    migrate.upgrade_legacy(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT web_mode, safesearch FROM child")).one() == ("off", 1)


def test_device_integrity_columns_migrated(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
        c.execute(text("INSERT INTO device (name) VALUES ('alt')"))
    migrate.upgrade_legacy(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT integrity_json, integrity_at FROM device")).one() == ("", None)


def _old_sqlite(tmp_path):
    """SQLite-Datenbank im alten Format: ohne Spalte device.platform, mit Daten."""
    eng = create_engine(f"sqlite:///{tmp_path}/kidctl.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, "
                       "password_hash VARCHAR(255), totp_secret VARCHAR(64), display_name VARCHAR(128) DEFAULT '', "
                       "created_at DATETIME)"))
        c.execute(text("ALTER TABLE parent ADD COLUMN email VARCHAR(255) DEFAULT ''"))
        c.execute(text("INSERT INTO parent (id, username, password_hash, totp_secret, created_at, email) "
                       "VALUES (7, 'mama', 'h', 'S', '2026-10-13 10:00:00', 'Mama@Example.org')"))
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, name VARCHAR(128), hostname VARCHAR(255) DEFAULT '', "
                       "token_hash VARCHAR(64) UNIQUE, created_at DATETIME, alert_after_hours INTEGER DEFAULT 48, "
                       "protocol_unsupported BOOLEAN DEFAULT 0, agent_version VARCHAR(32) DEFAULT '')"))
        c.execute(text("INSERT INTO device (id, name, token_hash, created_at) VALUES (3, 'PC', 'abc', '2026-10-14 08:00:00')"))
        c.execute(text("CREATE TABLE blocklist_state (id VARCHAR(64) PRIMARY KEY, sha256 VARCHAR(64) DEFAULT '', "
                       "count INTEGER DEFAULT 0, fetched_at DATETIME, error VARCHAR(255) DEFAULT '')"))
        c.execute(text("INSERT INTO blocklist_state (id, count) VALUES ('hagezi-light', 1000)"))   # Text-Schlüssel
    eng.dispose()
    return tmp_path / "kidctl.db"


def test_import_sqlite_dry_run_apply_and_sequences(client, tmp_path):
    from sqlalchemy import select

    from app.db import engine
    from conftest import SessionLocal
    from app.models import ROLE_OWNER, Device, Family, Membership, Parent
    from conftest import platform_db, reset_database

    path = _old_sqlite(tmp_path)
    reset_database()
    counts = migrate.import_sqlite(str(path), engine)            # Probelauf schreibt nichts
    assert counts["parent"] == 1 and counts["device"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(Parent)) is None
    migrate.import_sqlite(str(path), engine, apply=True)
    with SessionLocal() as db:
        dev = db.get(Device, 3)
        assert dev.name == "PC" and dev.platform == "linux"       # fehlende Spalte mit Standardwert ergänzt
        assert db.get(Parent, 7).username == "mama"
        assert db.get(Parent, 7).email_verified_at is not None                   # wie Migration 0003
        assert db.get(Family, 1) is not None
        assert db.scalar(select(Membership.role).where(Membership.parent_id == 7)) == ROLE_OWNER
    with platform_db() as db:
        db.add(Parent(username="papa", password_hash="h", totp_secret="T"))
        db.commit()
        assert db.scalar(select(Parent.id).where(Parent.username == "papa")) == 8   # ID-Folge nachgezogen
    with pytest.raises(RuntimeError, match="nicht leer"):
        migrate.import_sqlite(str(path), engine, apply=True)


def test_import_sqlite_rejects_duplicate_emails(client, tmp_path):
    from app.db import engine
    path = _old_sqlite(tmp_path)
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as c:
        c.execute(text("INSERT INTO parent (id, username, password_hash, totp_secret, created_at, email) "
                       "VALUES (8, 'papa', 'h', 'T', '2026-10-13 10:00:00', 'mama@example.org')"))
    eng.dispose()
    with pytest.raises(RuntimeError, match="mehrfach"):
        migrate.import_sqlite(str(path), engine)
