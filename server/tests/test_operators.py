"""Etappe 4.2: Betreiber-Accounts, Verwaltungsbereich /admin, Einladungscodes, IP-Beschränkung."""

import ipaddress
import re
from dataclasses import replace
from datetime import timedelta

import pyotp
import pytest

from app import operators
from app.config import settings
from app.models import Child, Device, Operator, OperatorSession, SignupCode, utcnow
from app.security import hash_password, new_totp_secret
from conftest import two_step_login, PASSWORD, add_family, csrf_from, platform_db
from app.tenant import family_session


@pytest.fixture
def operator(client):
    with platform_db() as db:
        op = Operator(username="betrieb", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(op)
        db.commit()
        return op


def admin_login(client, op, password=PASSWORD, code=None):
    return two_step_login(client, "/admin/login", op.username, password, code or pyotp.TOTP(op.totp_secret).now())


def test_admin_requires_operator_login(client, operator, parent):
    from conftest import login
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    login(client, parent)                                                   # Eltern-Sitzung gilt nicht
    assert client.get("/admin", follow_redirects=False).status_code == 303
    assert admin_login(client, operator, password="falsch").status_code == 401
    r = admin_login(client, operator)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "path=/admin" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert "Familien" in client.get("/admin").text
    assert client.get("/children", follow_redirects=False).status_code == 200   # Eltern-Sitzung unberührt
    client.cookies.delete("kidctl_session")
    assert client.get("/children", follow_redirects=False).headers["location"] == "/login"  # Betreiber ≠ Eltern
    tok = csrf_from(client.get("/admin").text)
    assert client.post("/admin/logout", data={"csrf_token": tok}, follow_redirects=False).status_code == 303
    with platform_db() as db:
        assert db.query(OperatorSession).count() == 0


def test_overview_shows_metadata_only(client, operator):
    fid = add_family("Geheimname")
    with family_session(fid) as db:
        child = Child(name="Geheimkind")
        db.add(child)
        db.add(Device(name="Geheimgerät", token_hash="x" * 64, agent_version="0.33.0",
                      last_seen_at=utcnow() - timedelta(days=3)))
        db.commit()
    admin_login(client, operator)
    page = client.get("/admin").text
    assert f"#{fid}" in page and "0.33.0" in page and "1 Gerät offline" in page
    assert "Geheimname" in page                                            # Familienname: Plattform-Schlüssel
    assert "Geheimkind" not in page and "Geheimgerät" not in page          # Kinder, Geräte: Schlüssel der Familie
    with platform_db() as db:
        row = next(r for r in operators.families(db) if r.id == fid)
    assert (row.children, row.devices, row.offline) == (1, 1, 1)


def test_signup_codes(client, operator):
    admin_login(client, operator)
    tok = csrf_from(client.get("/admin/codes").text)
    page = client.post("/admin/codes", data={"csrf_token": tok, "note": "Familie Müller"}).text
    code = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})", page).group(1)
    assert "Familie Müller" in page
    with platform_db() as db:
        stored = db.query(SignupCode).one()
        assert code.replace("-", "") not in stored.code_hash and stored.expires_at > utcnow() + timedelta(days=29)
        assert operators.find_code(db, code.lower().replace("-", " ")).id == stored.id   # tolerant bei Eingabe
        assert operators.find_code(db, "AAAA-BBBB-CCCC") is None
        stored.used_at = utcnow()
        db.commit()
        assert operators.find_code(db, code) is None                       # einmalig
    assert client.post(f"/admin/codes/{stored.id}/delete", data={"csrf_token": tok}).status_code == 400
    page = client.post("/admin/codes", data={"csrf_token": tok, "note": "x"}).text
    with platform_db() as db:
        other = db.query(SignupCode).filter(SignupCode.used_at.is_(None)).one()
    assert client.post(f"/admin/codes/{other.id}/delete", data={"csrf_token": tok},
                       follow_redirects=False).status_code == 303
    assert client.post("/admin/codes", data={"csrf_token": "falsch", "note": "x"}).status_code == 403


def test_admin_allow_nets(client, operator, monkeypatch):
    monkeypatch.setattr(operators, "settings", replace(settings, admin_allow_nets=(ipaddress.ip_network("10.0.0.0/8"),)))
    assert client.get("/admin/login").status_code == 404                    # testclient ≠ 10.x
    assert client.get("/admin/login", headers={"X-Forwarded-For": "10.1.2.3"}).status_code == 200
    assert operators.ip_allowed("10.9.9.9") and not operators.ip_allowed("192.168.1.1")


def test_admin_login_rate_limit(client, operator):
    for _ in range(5):
        assert admin_login(client, operator, password="falsch").status_code == 401
    assert admin_login(client, operator).status_code == 429


def test_cli_create_operator(client, monkeypatch, capsys):
    from argparse import Namespace
    from app import cli
    monkeypatch.setenv("KIDCTL_OPERATOR_USER", "admin")
    monkeypatch.setenv("KIDCTL_OPERATOR_PASSWORD", "x" * 12)
    assert cli.create_operator(Namespace(reset=False, role=None, email=None)) == 0
    assert cli.create_operator(Namespace(reset=False, role=None, email=None)) == 1                 # existiert
    with platform_db() as db:
        old = db.query(Operator).filter_by(username="admin").one().totp_secret
    assert cli.create_operator(Namespace(reset=True, role=None, email=None)) == 0
    with platform_db() as db:
        assert db.query(Operator).filter_by(username="admin").one().totp_secret != old
    monkeypatch.setenv("KIDCTL_OPERATOR_PASSWORD", "kurz")
    assert cli.create_operator(Namespace(reset=True, role=None, email=None)) == 1
