"""Sicherheitsprüfung 2026-11-08: jeder behobene Befund als Test gegen den ursprünglichen Angriff."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pyotp
from conftest import PASSWORD, csrf_from

from app import accounts
from app.middleware import MAX_BODY_BYTES
from app.security import client_ip

ENROLL = "/api/v1/enroll"


def _attempt(client, parent, password, code, ip):
    token = csrf_from(client.get("/login").text)
    return client.post("/login", data={"csrf_token": token, "username": "mama", "password": password, "totp": code},
                       headers={"X-Forwarded-For": ip}, follow_redirects=False)


def test_body_limit_by_length(client):
    r = client.post(ENROLL, content=b"x" * (MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_body_limit_without_length(client):
    def chunks():
        for _ in range(4):
            yield b"x" * MAX_BODY_BYTES
    r = client.post(ENROLL, content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_normal_request_passes_limit(client):
    assert client.post(ENROLL, json={"code": "AAAAAAAA", "hostname": "x"}).status_code == 400


def test_wrong_passwords_do_not_lock_out_parent(client, parent):
    for _ in range(5):
        assert _attempt(client, parent, "falsch-falsch-falsch", "000000", "10.2.0.1").status_code == 401
    assert _attempt(client, parent, "falsch-falsch-falsch", "000000", "10.2.0.1").status_code == 429   # IP gesperrt
    code = pyotp.TOTP(parent.totp_secret).now()
    assert _attempt(client, parent, PASSWORD, code, "10.3.0.1").status_code == 303                     # andere IP


def test_wrong_codes_lock_user_on_every_ip(client, parent):
    for i in range(5):
        assert _attempt(client, parent, PASSWORD, "000000", f"10.4.0.{i}").status_code == 401
    code = pyotp.TOTP(parent.totp_secret).now()
    assert _attempt(client, parent, PASSWORD, code, "10.5.0.1").status_code == 429


def test_user_limit_stops_distributed_guessing(client, parent):
    for i in range(50):
        _attempt(client, parent, "falsch-falsch-falsch", "000000", f"10.6.{i // 4}.{i}")
    assert _attempt(client, parent, "falsch-falsch-falsch", "000000", "10.7.0.1").status_code == 429


def test_client_ip_is_last_forwarded_entry():
    def req(*values):
        return SimpleNamespace(headers=SimpleNamespace(getlist=lambda name: list(values)),
                               client=SimpleNamespace(host="172.18.0.2"))
    assert client_ip(req("1.1.1.1, 9.9.9.9")) == "9.9.9.9"                # links steht, was der Client behauptet
    assert client_ip(req("1.1.1.1", "9.9.9.9")) == "9.9.9.9"
    assert client_ip(req()) == "172.18.0.2"


def test_spoofed_forwarded_for_does_not_bypass_limit(client):
    codes = [client.post(ENROLL, json={"code": "AAAAAAAA", "hostname": "x"},
                         headers={"X-Forwarded-For": f"10.8.0.{i}, 9.9.9.9"}).status_code for i in range(7)]
    assert codes[:5] == [400] * 5 and codes[5:] == [429, 429]


def test_reset_mail_runs_as_background_task(client, parent, monkeypatch):
    calls = []
    monkeypatch.setattr(accounts, "start_password_reset", lambda db, ident: calls.append(ident))
    monkeypatch.setattr("app.routes.auth.settings", SimpleNamespace(mail_enabled=True))
    token = csrf_from(client.get("/reset").text)
    r = client.post("/reset", data={"csrf_token": token, "ident": "mama"})
    assert r.status_code == 200 and calls == ["mama"]


def test_csp_on_every_response(client):
    for path in ("/login", "/healthz", "/static/app.js", "/gibtsnicht"):
        csp = client.get(path).headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp


def test_templates_need_no_inline_script():
    """Die CSP verbietet Inline-Skripte: Eintrag hier = kaputter Knopf im Browser."""
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    for f in templates.rglob("*.html"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", text, re.S), f.name
        assert not re.findall(r"<script(?![^>]*\bsrc=)", text), f.name
        assert "javascript:" not in text, f.name
