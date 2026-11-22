"""Update auf Knopfdruck: Knopf nur bei verfügbarem Update und fähigem, verbundenem Agenten; CSRF; Statusmeldungen."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from kidctl_common import protocol as p

from app import versions
from app.models import Device, utcnow
from conftest import SessionLocal, csrf_from, login, recv


@pytest.fixture
def release(monkeypatch):
    rel = versions.Release("0.46.0", utcnow())
    monkeypatch.setattr(versions, "latest", lambda repo=None: rel)
    monkeypatch.setattr(versions, "latest_windows", lambda repo=None: rel)
    return rel


@contextmanager
def agent(client, tok, version="0.45.2", features=(p.FEATURE_SELF_UPDATE,)):
    r = client.post("/devices/pair", data={"name": "pc", "csrf_token": tok})
    code = r.text.split("sudo kidctl enroll http://testserver ")[1][:8]
    enr = client.post(p.ENROLL_PATH, json={"code": code, "hostname": "pc"}).json()
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {enr['token']}"}) as ws:
        ws.send_text(p.Hello(protocol_version=1, agent_version=version, hostname="pc",
                             features=list(features)).model_dump_json())
        assert isinstance(recv(ws), p.Welcome)
        yield ws, enr


def _reconnect(client, enr, version):
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {enr['token']}"}) as ws:
        ws.send_text(p.Hello(protocol_version=1, agent_version=version, hostname="pc",
                             features=[p.FEATURE_SELF_UPDATE]).model_dump_json())
        assert isinstance(recv(ws), p.Welcome)


def _status(ws, state, detail=""):
    ws.send_text(p.UpdateStatus(state=state, detail=detail).model_dump_json())
    ws.send_text(p.Ping().model_dump_json())
    assert isinstance(recv(ws), p.Pong)


def _dev(dev_id) -> Device:
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        db.expunge(d)
        return d


def test_update_button_flow(client, parent, release):
    login(client, parent)
    tok = csrf_from(client.get("/devices").text)
    with agent(client, tok) as (ws, enr):
        dev_id = enr["device_id"]
        page = client.get(f"/devices/{dev_id}").text
        assert f'action="/devices/{dev_id}/update"' in page and "Update verfügbar: 0.46.0." in page
        # ohne CSRF-Token: abgelehnt, nichts gesendet
        assert client.post(f"/devices/{dev_id}/update").status_code in (400, 403)
        assert _dev(dev_id).update_state == ""

        r = client.post(f"/devices/{dev_id}/update", data={"csrf_token": tok}, follow_redirects=False)
        assert r.status_code == 303
        assert recv(ws) == p.UpdateNow()   # nur Auslöser, keine Felder
        d = _dev(dev_id)
        assert d.update_state == "running" and d.update_from == "0.45.2"
        page = client.get(f"/devices/{dev_id}").text
        assert "Update läuft" in page and f'action="/devices/{dev_id}/update"' not in page   # entprellt

        # zweiter Klick innerhalb der Sperrzeit: kein weiteres update_now
        client.post(f"/devices/{dev_id}/update", data={"csrf_token": tok})
        _status(ws, "failed", "pacman: Datenbank gesperrt")
        assert "Update fehlgeschlagen: pacman: Datenbank gesperrt" in client.get(f"/devices/{dev_id}").text

        # nach Fehler wieder auslösbar
        client.post(f"/devices/{dev_id}/update", data={"csrf_token": tok})
        assert recv(ws) == p.UpdateNow()
        _status(ws, "running")
    # Agent startet mit neuer Version neu -> erledigt
    _reconnect(client, enr, "0.46.0")
    d = _dev(dev_id)
    assert d.update_state == "done" and d.update_detail is None
    page = client.get(f"/devices/{dev_id}").text
    assert "Aktualisiert von 0.45.2 auf 0.46.0" in page and f'action="/devices/{dev_id}/update"' not in page


def test_no_button_without_feature_update_or_connection(client, parent, release):
    login(client, parent)
    tok = csrf_from(client.get("/devices").text)
    with agent(client, tok, features=()) as (ws, enr):   # älterer Agent
        dev_id = enr["device_id"]
        page = client.get(f"/devices/{dev_id}").text
        assert f'action="/devices/{dev_id}/update"' not in page and "Systemupdate" in page
        client.post(f"/devices/{dev_id}/update", data={"csrf_token": tok})
        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(recv(ws), p.Pong)   # kein update_now dazwischen
        assert _dev(dev_id).update_state == ""
    with agent(client, tok, version="0.46.0") as (ws, enr):   # bereits aktuell
        page = client.get(f"/devices/{enr['device_id']}").text
        assert f'action="/devices/{enr["device_id"]}/update"' not in page
    _reconnect(client, enr, "0.45.0")
    # offline: Knopf aus, Hinweis
    page = client.get(f"/devices/{enr['device_id']}").text
    assert f'action="/devices/{enr["device_id"]}/update"' not in page and "Gerät offline" in page
    client.post(f"/devices/{enr['device_id']}/update", data={"csrf_token": tok})
    assert _dev(enr["device_id"]).update_state == ""


def test_status_without_request_ignored_and_timeout(client, parent, release):
    login(client, parent)
    tok = csrf_from(client.get("/devices").text)
    with agent(client, tok) as (ws, enr):
        dev_id = enr["device_id"]
        _status(ws, "failed", "unaufgefordert")
        assert _dev(dev_id).update_state == ""
        client.post(f"/devices/{dev_id}/update", data={"csrf_token": tok})
        assert recv(ws) == p.UpdateNow()
        with SessionLocal() as db:
            db.get(Device, dev_id).update_requested_at = utcnow() - timedelta(minutes=20)
            db.commit()
        page = client.get(f"/devices/{dev_id}").text
        assert "Update ohne Rückmeldung" in page and f'action="/devices/{dev_id}/update"' in page
        _status(ws, "current")
        assert "Kein neueres Paket" in client.get(f"/devices/{dev_id}").text
