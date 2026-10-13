"""Smoke-Tests PWA & Web Push: Abo-Verwaltung, CSRF, echter verschlüsselter Versand, Aufräumen."""

import base64
import json
import os
import time
import uuid

import http_ece
import pywebpush
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from kidctl_common import protocol as p

from app.db import SessionLocal
from app.models import PushSubscription
from app.schedule import today_local

from conftest import csrf_from, login
from test_chat import _recv, connected


def b64(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def make_subscription(endpoint="https://push.example.org/send/abc"):
    """Wie ein Browser: ECDH-Schlüssel + Auth-Secret des Empfängers."""
    key = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    pub = key.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    return key, auth, {"endpoint": endpoint, "keys": {"p256dh": b64(pub), "auth": b64(auth)}}


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.reason = "Gone" if status == 410 else "Created"
        self.text = ""
        self.headers = {}


def capture(monkeypatch, status=201):
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        sent.append({"url": url, "data": data, "headers": headers})
        return FakeResponse(status)
    monkeypatch.setattr(pywebpush.requests, "post", fake_post)
    return sent


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _subscribe(client, sub):
    tok = csrf_from(client.get("/children").text)
    return client.post("/push/subscribe", json=sub, headers={"X-CSRF-Token": tok})


def test_pwa_files(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert "showNotification" in sw.text
    man = client.get("/manifest.webmanifest")
    assert man.status_code == 200 and man.json()["display"] == "standalone"
    for icon in man.json()["icons"]:
        assert client.get(icon["src"]).headers["content-type"] == "image/png"


def test_subscribe_requires_csrf_and_https(client, parent):
    login(client, parent)
    _, _, sub = make_subscription()
    assert client.post("/push/subscribe", json=sub).status_code == 403
    assert client.post("/push/subscribe", json=sub, headers={"X-CSRF-Token": "falsch"}).status_code == 403
    bad = {**sub, "endpoint": "http://intern.local/x"}
    assert _subscribe(client, bad).status_code == 422
    assert _subscribe(client, sub).status_code == 200
    assert _subscribe(client, sub).status_code == 200  # idempotent
    with SessionLocal() as db:
        assert len(db.scalars(select(PushSubscription)).all()) == 1
    assert "Aktive Geräte für deinen Account: <strong>1</strong>" in client.get("/settings").text

    tok = csrf_from(client.get("/children").text)
    client.post("/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers={"X-CSRF-Token": tok})
    with SessionLocal() as db:
        assert db.scalar(select(PushSubscription)) is None


def test_test_push_is_encrypted_and_signed(client, parent, monkeypatch):
    login(client, parent)
    key, auth, sub = make_subscription()
    _subscribe(client, sub)
    sent = capture(monkeypatch)

    tok = csrf_from(client.get("/settings").text)
    r = client.post("/push/test", data={"csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?sent=1"

    req = sent[0]
    assert req["url"] == sub["endpoint"]
    assert req["headers"]["content-encoding"] == "aes128gcm"
    vapid = req["headers"]["authorization"]
    assert vapid.startswith("vapid t=") and f"k={os.environ['VAPID_PUBLIC_KEY']}" in vapid
    payload = json.loads(http_ece.decrypt(req["data"], private_key=key, auth_secret=auth,
                                          version="aes128gcm"))
    assert payload["title"] == "kidctl" and payload["url"] == "/settings"


def test_child_message_triggers_push(client, parent, monkeypatch):
    sent = capture(monkeypatch)
    with connected(client, parent) as (ws, child_id, _):
        key, auth, sub = make_subscription()
        _subscribe(client, sub)
        ws.send_text(p.ExtensionRequest(client_msg_id=uuid.uuid4().hex, username="max",
                                        day=today_local(),
                                        minutes=30, reason="Bitte!").model_dump_json())
        _recv(ws), _recv(ws)
        assert _wait(lambda: len(sent) == 1)
        payload = json.loads(http_ece.decrypt(sent[0]["data"], private_key=key, auth_secret=auth,
                                              version="aes128gcm"))
        assert payload["title"] == "Max bittet um Verlängerung (+30 min)"
        assert payload["body"] == "Bitte!" and payload["url"].startswith(f"/children/{child_id}/chat#m")


def test_gone_subscription_removed(client, parent, monkeypatch):
    login(client, parent)
    _, _, sub = make_subscription()
    _subscribe(client, sub)
    capture(monkeypatch, status=410)
    tok = csrf_from(client.get("/settings").text)
    r = client.post("/push/test", data={"csrf_token": tok}, follow_redirects=False)
    assert r.headers["location"] == "/settings?sent=0"
    with SessionLocal() as db:
        assert db.scalar(select(PushSubscription)) is None
