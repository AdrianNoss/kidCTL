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


os.environ.update(_vapid_env())
os.environ.update({
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

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Parent  # noqa: E402
from app.security import hash_password, new_totp_secret  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def parent(client):
    with SessionLocal() as db:
        p = Parent(username="mama", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(p)
        db.commit()
        return p


def csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    i = html.index(marker) + len(marker)
    return html[i:html.index('"', i)]


def login(client, parent, password=PASSWORD, code=None):
    with SessionLocal() as db:  # Replay-Schutz für wiederholte Test-Logins zurücksetzen
        db.get(Parent, parent.id).totp_last_step = 0
        db.commit()
    page = client.get("/login")
    return client.post("/login", data={
        "csrf_token": csrf_from(page.text), "username": parent.username, "password": password,
        "totp": code or pyotp.TOTP(parent.totp_secret).now(),
    }, follow_redirects=False)


def recv(ws, keep_policy=False):
    """Nächste Server-Nachricht; Policy/UsageUpdate werden übersprungen, außer keep_policy."""
    from kidctl_common import protocol as p
    while True:
        msg = p.server_message_adapter.validate_json(ws.receive_text())
        if keep_policy or not isinstance(msg, (p.Policy, p.UsageUpdate)):
            return msg
