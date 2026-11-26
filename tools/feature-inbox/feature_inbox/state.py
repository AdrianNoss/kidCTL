"""Lokaler Zustand (SQLite unter ``./data/feature-inbox/``): verarbeitete Request-IDs, intern gespeicherte
Kontaktadressen und Ereignisse für die Tagesübersicht. Liegt nie im Repo und nie in GitHub."""

from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    issue_number INTEGER NOT NULL,
    contact TEXT,               -- nur bei „Rückfragen erlaubt“, nie im Issue
    duplicates TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    at REAL NOT NULL,
    kind TEXT NOT NULL,         -- new | duplicate | unsorted | error
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class State:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        if path != ":memory:":
            os.chmod(path, 0o600)

    def known(self, request_id: str) -> int | None:
        row = self.db.execute("SELECT issue_number FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return row[0] if row else None

    def record(self, request_id: str, issue_number: int, contact: str | None, duplicates: list[int]) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO requests VALUES (?,?,?,?,?)",
                (request_id, issue_number, contact, ",".join(map(str, duplicates)), time.time()),
            )

    def event(self, kind: str, detail: str = "") -> None:
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?,?,?)", (time.time(), kind, detail))

    def events_since(self, since: float) -> list[tuple[str, str]]:
        return self.db.execute("SELECT kind, detail FROM events WHERE at>=? ORDER BY at", (since,)).fetchall()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
