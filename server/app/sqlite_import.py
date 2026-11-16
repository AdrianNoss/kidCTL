"""Übernahme einer alten SQLite-Datenbank (bis 0.32.x) nach PostgreSQL.

    python -m app.cli import-sqlite /data/kidctl.db           Probelauf: zeigt, was übernommen würde
    python -m app.cli import-sqlite /data/kidctl.db --apply   übernimmt (Ziel muss leer sein)

Gearbeitet wird auf einer Kopie der SQLite-Datei: Zuerst zieht ``upgrade_legacy`` sie mit den früheren
Spaltenmigrationen auf den letzten SQLite-Stand (Muster: Spalte per ``PRAGMA table_info`` prüfen -> ``ALTER TABLE`` ->
verifizieren -> bei Fehlschlag ``RuntimeError``), fehlende Tabellen legt ``create_all`` an. Dann werden alle Tabellen
in Abhängigkeitsreihenfolge in einer Transaktion kopiert und die ID-Folgen in PostgreSQL nachgezogen.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, Integer, text

log = logging.getLogger("kidctl.sqlite_import")

# (Tabelle, Spalte, DDL), z. B. ("device", "alert_hours", "INTEGER NOT NULL DEFAULT 48")
COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # 2026-10-13: Chat/Verlängerungen
    ("message", "approved_minutes", "INTEGER"),
    ("message", "approved_window_end", "TIME"),
    ("message", "device_id", "INTEGER REFERENCES device(id) ON DELETE SET NULL"),
    ("message", "client_msg_id", "VARCHAR(64)"),
    # 2026-10-13: Policy-Verteilung, Offline-Alarm
    ("device", "policy_version", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("device", "offline_alerted_at", "DATETIME"),
    # 2026-10-13: Programm-Whitelist
    ("child", "program_mode", "VARCHAR(16) NOT NULL DEFAULT 'off'"),
    # 2026-10-14: Website-Filter (neue Spalte statt web_filter_mode, deren Standard „whitelist“ war)
    ("child", "web_mode", "VARCHAR(16) NOT NULL DEFAULT 'off'"),
    ("child", "safesearch", "BOOLEAN NOT NULL DEFAULT 1"),
    # 2026-10-14: Integritätsprüfung
    ("device", "integrity_json", "TEXT NOT NULL DEFAULT ''"),
    ("device", "integrity_at", "DATETIME"),
    # 2026-10-15: „Jetzt sperren“
    ("child", "locked", "BOOLEAN NOT NULL DEFAULT 0"),
    ("child", "locked_until", "DATETIME"),
    # 2026-10-16: Vorwarnzeit bei „Jetzt sperren“
    ("child", "lock_grace_minutes", "INTEGER NOT NULL DEFAULT 1"),
    # 2026-10-16: Kategorien für die gruppierte Programmliste
    ("device_app", "categories", "TEXT NOT NULL DEFAULT ''"),
    # 2026-10-19: Spielzeit je Spiel (Name vom Agenten, Spiele stehen nicht im Katalog)
    ("app_usage", "name", "VARCHAR(255) NOT NULL DEFAULT ''"),
    # 2026-10-19: Profil, Passwort vergessen
    ("parent", "email", "VARCHAR(255) NOT NULL DEFAULT ''"),
    # 2026-10-20: Nutzungsbericht per E-Mail
    ("parent", "report_interval", "VARCHAR(16) NOT NULL DEFAULT ''"),
    ("parent", "report_sent", "VARCHAR(16) NOT NULL DEFAULT ''"),
    # 2026-10-20: Freigabe-Anfragen für Programme/Websites
    ("message", "access_kind", "VARCHAR(16)"),
    ("message", "access_key", "VARCHAR(512)"),
    ("message", "access_name", "VARCHAR(255)"),
    ("message", "access_scope", "VARCHAR(16)"),
    # 2026-10-20: Spiele-Limit
    ("weekday_rule", "game_minutes", "INTEGER"),
    ("day_exception", "extra_game_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("usage_record", "game_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("message", "request_target", "VARCHAR(8)"),
    # 2026-10-21: Sprache (WebUI, Mails, Push)
    ("parent", "language", "VARCHAR(8) NOT NULL DEFAULT ''"),
    ("parent", "detected_language", "VARCHAR(8) NOT NULL DEFAULT ''"),
    ("device_app", "name_en", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("blocked_event", "detail_code", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("blocked_event", "detail_arg", "VARCHAR(255) NOT NULL DEFAULT ''"),
    # 2026-10-21: Hinweis auf veralteten Agenten
    ("device", "update_alerted", "VARCHAR(32) NOT NULL DEFAULT ''"),
    # 2026-11-04: Windows-Clients
    ("device", "platform", "VARCHAR(16) NOT NULL DEFAULT 'linux'"),
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


def upgrade_legacy(engine: Engine) -> None:
    """Alte SQLite-Datenbank auf den letzten SQLite-Spaltenstand bringen."""
    if engine.url.get_backend_name() != "sqlite":
        raise RuntimeError("upgrade_legacy unterstützt nur SQLite")
    for table, column, ddl in COLUMN_MIGRATIONS:
        ensure_column(engine, table, column, ddl)


def _too_long(table, rows) -> list[str]:
    """Werte, die in PostgreSQL nicht in die VARCHAR-Länge passen (SQLite prüft Längen nicht)."""
    out = []
    for col in table.columns:
        length = getattr(col.type, "length", None)
        if not length:
            continue
        for row in rows:
            value = row[col.name]
            if isinstance(value, str) and len(value) > length:
                out.append(f"{table.name}.{col.name} (id {row.get('id', '?')}): {len(value)} > {length} Zeichen")
    return out


def import_sqlite(path: str, target: Engine, apply: bool = False) -> dict[str, int]:
    """Alle Tabellen aus ``path`` nach ``target`` kopieren. Gibt die Zeilenzahl je Tabelle zurück."""
    import shutil
    import tempfile
    from pathlib import Path

    from alembic import command
    from sqlalchemy import Text, create_engine, func, insert, select, type_coerce

    from .crypto import _Encrypted, family as crypto_family

    from . import models  # noqa: F401  (Tabellen registrieren)
    from .db import Base, alembic_config

    src_file = Path(path)
    if not src_file.is_file():
        raise RuntimeError(f"SQLite-Datei nicht gefunden: {src_file}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "kidctl.db"
        shutil.copy2(src_file, copy)
        for suffix in ("-wal", "-shm"):   # offene WAL-Dateien mitnehmen, sonst fehlen die letzten Schreibvorgänge
            if Path(f"{src_file}{suffix}").exists():
                shutil.copy2(f"{src_file}{suffix}", f"{copy}{suffix}")
        src = create_engine(f"sqlite:///{copy}")
        upgrade_legacy(src)
        Base.metadata.create_all(src)
        with src.connect() as conn:
            data = {}
            for t in Base.metadata.sorted_tables:   # nur vorhandene Spalten lesen, fehlende bekommen den Standardwert
                present = set(_columns(conn, t.name))
                # verschlüsselte Spalten roh lesen (in SQLite steht Klartext), alle anderen mit ihrem Typ
                cols = [type_coerce(c, Text).label(c.name) if isinstance(c.type, _Encrypted) else c
                        for c in t.columns if c.name in present]
                data[t.name] = [dict(r) for r in conn.execute(select(*cols)).mappings()]
        src.dispose()

    # Alte Installationen kennen keine Familien: alles gehört Familie 1, der älteste Account wird Inhaber
    from .models import ROLE_OWNER, ROLE_PARENT, FamilyScoped
    if not data["family"] and (data["parent"] or data["child"] or data["device"]):
        data["family"] = [{"id": 1, "name": "", "created_at": None}]
        owner = min((r["id"] for r in data["parent"]), default=None)
        data["membership"] = [{"family_id": 1, "parent_id": r["id"], "role": ROLE_OWNER if r["id"] == owner else ROLE_PARENT}
                              for r in data["parent"]]
        for t in Base.metadata.sorted_tables:
            if "family_id" in t.c and t.name != "membership":
                for row in data[t.name]:
                    row["family_id"] = 1
    data["family"] = [{k: v for k, v in r.items() if v is not None} for r in data["family"]]
    import os
    from . import crypto
    for fam in data["family"]:   # Core-Insert: das Ereignis „neue Familie bekommt Schlüssel“ läuft hier nicht
        if not fam.get("wrapped_key"):
            dek = os.urandom(64)
            fam["wrapped_key"] = crypto.wrap_family_key(fam["id"], dek)
            if apply:
                crypto.remember(fam["id"], dek)
    # Wie Migration 0003: vorhandene Adressen gelten als bestätigt; jede Adresse nur einmal (ohne Groß/klein)
    from .models import utcnow
    seen: dict[str, str] = {}
    from .crypto import email_index
    for row in data["parent"]:
        mail = (row.get("email") or "").strip()
        row["email_index"] = email_index(mail)   # Suchwert für die Anmeldung (Core-Insert: kein Ereignis)
        if not mail:
            continue
        if mail.lower() in seen:
            raise RuntimeError(f"E-Mail-Adresse {mail} mehrfach vergeben ({seen[mail.lower()]}, {row['username']}) – "
                               "erst im alten System ändern.")
        seen[mail.lower()] = row["username"]
        row.setdefault("email_verified_at", None)
        row["email_verified_at"] = row["email_verified_at"] or utcnow()
    counts = {name: len(rows) for name, rows in data.items()}
    problems = [msg for t in Base.metadata.sorted_tables for msg in _too_long(t, data[t.name])]
    if problems:
        raise RuntimeError("Werte zu lang für PostgreSQL:\n  " + "\n  ".join(problems))
    if not apply:
        return counts

    command.upgrade(alembic_config(target.url.render_as_string(hide_password=False)), "head")
    with target.begin() as conn:
        filled = [t.name for t in Base.metadata.sorted_tables if conn.execute(select(func.count()).select_from(t)).scalar()]
        if filled:
            raise RuntimeError(f"Ziel-Datenbank ist nicht leer ({', '.join(filled)}) – Übernahme abgebrochen.")
        for table in Base.metadata.sorted_tables:
            rows = data[table.name]
            with crypto_family(1):   # alte Installationen: alles Familie 1, verschlüsselt mit deren Schlüssel
                for i in range(0, len(rows), 1000):
                    conn.execute(insert(table), rows[i:i + 1000])
            if "id" in table.c and rows and isinstance(table.c.id.type, Integer):   # Text-Schlüssel: keine Folge
                conn.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
                                  f"(SELECT max(id) FROM \"{table.name}\"))"))
    log.warning("SQLite-Übernahme aus %s: %d Zeilen in %d Tabellen", src_file, sum(counts.values()), len(counts))
    return counts
