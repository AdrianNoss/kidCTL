import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="kidctl-test-")
def _vapid_env() -> dict:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def b64(d):
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    k = ec.generate_private_key(ec.SECP256R1())
    return {
        "VAPID_PRIVATE_KEY": b64(k.private_numbers().private_value.to_bytes(32, "big")),
        "VAPID_PUBLIC_KEY": b64(k.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
        "VAPID_SUBJECT": "mailto:test@example.org",
    }


# Tests laufen gegen eine eigene PostgreSQL-Datenbank (Entwicklung: Container kidctl-pg, siehe dev/README.md)
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL",
                                   "postgresql+psycopg://kidctl:kidctl-dev@127.0.0.1:55432/kidctl_test")


def _create_test_database(url: str) -> None:
    import psycopg

    base, name = url.replace("postgresql+psycopg://", "postgresql://").rsplit("/", 1)
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(f'CREATE DATABASE "{name}"')


_create_test_database(TEST_DATABASE_URL)
# Backups: ohne lokale PostgreSQL-Programme pg_dump/pg_restore im Container aufrufen
import shutil  # noqa: E402

if not shutil.which("pg_dump"):
    _wrapper = Path(__file__).with_name("podman_pg.sh")
    os.environ.setdefault("KIDCTL_PG_DUMP", f"{_wrapper} pg_dump")
    os.environ.setdefault("KIDCTL_PG_RESTORE", f"{_wrapper} pg_restore")
os.environ.update(_vapid_env())
os.environ.update({
    "DATABASE_URL": TEST_DATABASE_URL,
    "KIDCTL_DEV": "1",
    "SECRET_KEY": "test-secret",
    "KIDCTL_DATA_DIR": _tmp,
    "BASE_URL": "http://testserver",
    "COOKIE_SECURE": "0",
})

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "common")]

import pyotp  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.db import SessionLocal as _PlainSession  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Parent  # noqa: E402
from app.security import hash_password, new_totp_secret  # noqa: E402

PASSWORD = "correct horse battery"


def reset_database() -> None:
    """Schema auf Alembic-Stand bringen und alle Tabellen leeren (schneller als neu anlegen)."""
    from sqlalchemy import text

    from app.db import init_db
    init_db()
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


TEST_FAMILY = 1


def SessionLocal():  # noqa: N802  (ersetzt app.db.SessionLocal in den Tests)
    """Sitzung im Kontext der Testfamilie 1 – wie eine angemeldete Familie."""
    from app.tenant import set_family
    db = _PlainSession()
    set_family(db, TEST_FAMILY)
    return db


def platform_db():
    from app.tenant import set_platform
    db = _PlainSession()
    set_platform(db)
    return db


def add_family(name: str = "") -> int:
    from app.models import Family
    with platform_db() as db:
        fam = Family(name=name)
        db.add(fam)
        db.commit()
        return fam.id


def add_parent(**fields):
    """Weiteren Eltern-Account in der Testfamilie anlegen (mit Mitgliedschaft)."""
    from app.models import ROLE_PARENT, Membership
    fields.setdefault("password_hash", hash_password(PASSWORD))
    fields.setdefault("totp_secret", new_totp_secret())
    if fields.get("email"):
        from app.models import utcnow
        fields.setdefault("email_verified_at", utcnow())
    with SessionLocal() as db:
        p = Parent(**fields)
        db.add(p)
        db.flush()
        db.add(Membership(parent_id=p.id, role=ROLE_PARENT))
        db.commit()
        return p


@pytest.fixture
def client():
    reset_database()
    add_family("Testfamilie")
    with TestClient(app, headers={"Accept-Language": "de-DE,de;q=0.9"}) as c:
        yield c


@pytest.fixture
def parent(client):
    from app.models import ROLE_OWNER, Membership
    with SessionLocal() as db:
        p = Parent(username="mama", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(p)
        db.flush()
        db.add(Membership(parent_id=p.id, role=ROLE_OWNER))
        db.commit()
        return p


def csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    i = html.index(marker) + len(marker)
    return html[i:html.index('"', i)]


def two_step_login(client, base, username, password, code, headers=None):
    """Login in zwei Schritten (``base``: ``/login`` bzw. ``/admin/login``). Gibt die letzte Antwort zurück:
    bei falschem Passwort die von Schritt 1, sonst die von Schritt 2."""
    h = headers or {}
    page = client.get(base, headers=h)
    r = client.post(base, data={"csrf_token": csrf_from(page.text), "username": username, "password": password},
                    headers=h, follow_redirects=False)
    if r.status_code != 303 or r.headers["location"] != base + "/code":
        return r
    page = client.get(base + "/code", headers=h)
    return client.post(base + "/code", data={"csrf_token": csrf_from(page.text), "totp": code}, headers=h,
                       follow_redirects=False)


def login(client, parent, password=PASSWORD, code=None):
    with SessionLocal() as db:  # Replay-Schutz für wiederholte Test-Logins zurücksetzen
        db.get(Parent, parent.id).totp_last_step = 0
        db.commit()
    return two_step_login(client, "/login", parent.username, password,
                          code or pyotp.TOTP(parent.totp_secret).now())


def recv(ws, keep_policy=False):
    """Nächste Server-Nachricht; Policy/UsageUpdate werden übersprungen, außer keep_policy."""
    from kidctl_common import protocol as p
    while True:
        msg = p.server_message_adapter.validate_json(ws.receive_text())
        if keep_policy or not isinstance(msg, (p.Policy, p.UsageUpdate)):
            return msg
