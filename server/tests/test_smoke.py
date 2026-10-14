"""End-to-end-Smoke-Tests: Login mit TOTP, CSRF, Rate-Limit, Kopplung, WebSocket, Widerruf."""

import pyotp
import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from kidctl_common import protocol as p

from app.db import SessionLocal
from app.models import Child, Device, Parent

from conftest import PASSWORD, csrf_from, login, recv


def test_redirect_without_session(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_success_and_logout(client, parent):
    r = login(client, parent)
    assert r.status_code == 303 and r.headers["location"] == "/"
    home = client.get("/")
    assert home.status_code == 200 and "Übersicht" in home.text
    r = client.post("/logout", data={"csrf_token": csrf_from(home.text)}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_wrong_totp_and_password(client, parent):
    assert login(client, parent, code="000000").status_code == 401
    assert login(client, parent, password="falsch").status_code == 401


def test_totp_replay_rejected(client, parent):
    code = pyotp.TOTP(parent.totp_secret).now()
    assert login(client, parent, code=code).status_code == 303
    client.cookies.clear()
    page = client.get("/login")
    r = client.post("/login", data={"csrf_token": csrf_from(page.text), "username": "mama",
                                    "password": PASSWORD, "totp": code}, follow_redirects=False)
    assert r.status_code == 401


def test_login_rate_limit(client, parent):
    for _ in range(5):
        assert login(client, parent, password="falsch").status_code == 401
    # Auch korrekte Daten werden jetzt abgewiesen
    assert login(client, parent).status_code == 429


def test_login_requires_csrf(client, parent):
    client.get("/login")
    r = client.post("/login", data={"username": "mama", "password": PASSWORD,
                                    "totp": pyotp.TOTP(parent.totp_secret).now()})
    assert r.status_code == 403


def test_actions_require_csrf(client, parent):
    login(client, parent)
    assert client.post("/children", data={"name": "Max"}).status_code == 403
    assert client.post("/children", data={"name": "Max", "csrf_token": "x"}).status_code == 403
    tok = csrf_from(client.get("/children").text)
    r = client.post("/children", data={"name": "Max", "csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 303
    assert "Max" in client.get("/children").text


def test_foreign_origin_rejected(client, parent):
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    r = client.post("/children", data={"name": "X", "csrf_token": tok},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def _pair(client) -> str:
    tok = csrf_from(client.get("/devices").text)
    r = client.post("/devices/pair", data={"name": "Laptop Max", "csrf_token": tok})
    assert r.status_code == 200
    marker = "sudo kidctl enroll http://testserver "
    i = r.text.index(marker) + len(marker)
    return r.text[i:i + 8]


def test_pairing_websocket_mapping_revoke(client, parent):
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})

    code = _pair(client)
    # Code ist einmalig; Kleinschreibung/Bindestrich werden toleriert
    r = client.post(p.ENROLL_PATH, json={"code": f"{code[:4]}-{code[4:]}".lower(), "hostname": "max-pc"})
    assert r.status_code == 200, r.text
    enrolled = p.EnrollResponse.model_validate(r.json())
    assert client.post(p.ENROLL_PATH, json={"code": code, "hostname": "x"}).status_code == 400

    with SessionLocal() as db:
        assert db.get(Device, enrolled.device_id).token_hash != enrolled.token  # nur Hash gespeichert

    headers = {"Authorization": f"Bearer {enrolled.token}"}
    with client.websocket_connect(p.WS_PATH, headers=headers) as ws:
        ws.send_text(p.Hello(protocol_version=p.PROTOCOL_VERSION, agent_version="0.1.0",
                             hostname="max-pc").model_dump_json())
        welcome = recv(ws)
        assert isinstance(welcome, p.Welcome) and welcome.device_id == enrolled.device_id

        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(recv(ws), p.Pong)

        ws.send_text(p.ReportUsers(users=[p.LocalUser(username="max", uid=1001, full_name="Max"),
                                          p.LocalUser(username="papa", uid=1000)]).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(recv(ws), p.Pong)  # Bericht verarbeitet

        page = client.get(f"/devices/{enrolled.device_id}")
        assert "online" in page.text and "max" in page.text and "papa" in page.text

        with SessionLocal() as db:
            dev = db.get(Device, enrolled.device_id)
            users = {u.username: u for u in dev.users}
            child_id = db.scalar(select(Child.id))
        form = {"csrf_token": csrf_from(page.text), f"user_{users['max'].id}": str(child_id),
                f"user_{users['papa'].id}": ""}
        client.post(f"/devices/{enrolled.device_id}/mapping", data=form)
        with SessionLocal() as db:
            users = {u.username: u for u in db.get(Device, enrolled.device_id).users}
            assert users["max"].child_id == child_id and users["papa"].child_id is None

        # Widerruf trennt die laufende Verbindung
        client.post(f"/devices/{enrolled.device_id}/revoke", data={"csrf_token": csrf_from(page.text)})
        with pytest.raises(WebSocketDisconnect) as exc:
            recv(ws)  # vorher evtl. noch die Policy nach der Zuordnung
        assert exc.value.code == p.CLOSE_REVOKED

    # Widerrufenes Token wird abgewiesen
    with client.websocket_connect(p.WS_PATH, headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == p.CLOSE_UNAUTHORIZED


def test_websocket_unsupported_version(client, parent):
    login(client, parent)
    code = _pair(client)
    token = client.post(p.ENROLL_PATH, json={"code": code, "hostname": "alt"}).json()["token"]
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.send_text(p.Hello(protocol_version=0, agent_version="0.0.1", hostname="alt").model_dump_json())
        err = recv(ws)
        assert isinstance(err, p.Error) and err.code == "unsupported_version"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == p.CLOSE_UNSUPPORTED_VERSION
    devs = client.get("/devices").text
    assert "Agent veraltet" in devs


def test_websocket_requires_hello(client, parent):
    login(client, parent)
    token = client.post(p.ENROLL_PATH, json={"code": _pair(client), "hostname": "h"}).json()["token"]
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.send_text('{"type": "ping"}')
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == p.CLOSE_PROTOCOL_ERROR


def test_enroll_rate_limit(client):
    for _ in range(5):
        assert client.post(p.ENROLL_PATH, json={"code": "AAAAAAAA", "hostname": "h"}).status_code == 400
    assert client.post(p.ENROLL_PATH, json={"code": "AAAAAAAA", "hostname": "h"}).status_code == 429


def test_add_second_parent_shows_totp(client, parent):
    login(client, parent)
    tok = csrf_from(client.get("/parents").text)
    r = client.post("/parents", data={"csrf_token": tok, "username": "papa",
                                      "password": "kurz"})
    assert r.status_code == 400
    r = client.post("/parents", data={"csrf_token": tok, "username": "papa",
                                      "password": "ein-langes-passwort"})
    assert r.status_code == 200 and "<svg" in r.text
    with SessionLocal() as db:
        papa = db.scalar(select(Parent).where(Parent.username == "papa"))
    assert papa.totp_secret in r.text
    client.cookies.clear()
    page = client.get("/login")
    r = client.post("/login", data={"csrf_token": csrf_from(page.text), "username": "papa",
                                    "password": "ein-langes-passwort",
                                    "totp": pyotp.TOTP(papa.totp_secret).now()}, follow_redirects=False)
    assert r.status_code == 303


def test_repo_served_statically(client):
    from pathlib import Path
    from app.config import settings
    repo = Path(settings.data_dir) / "repo"
    (repo / "kidctl.db.tar.zst").write_bytes(b"db")
    link = repo / "kidctl.db"
    if not link.exists():
        link.symlink_to("kidctl.db.tar.zst")          # wie von repo-add angelegt
    assert client.get("/repo/kidctl.db").content == b"db"
    assert client.get("/repo/../config.py").status_code == 404
