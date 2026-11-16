"""Mandantentrennung: Eine Familie sieht und ändert nie Daten einer anderen – über jede Seite und Aktion."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import push
from app.db import SessionLocal as PlainSession
from app.models import (
    ROLE_OWNER, BlockedEvent, Child, DayException, Device, DeviceUser, Membership, Message, PairingCode, Parent,
    PushSubscription, utcnow,
)
from app.security import hash_password, new_totp_secret, token_hash
from app.tenant import TenantError, family_session
from conftest import PASSWORD, SessionLocal, add_family, csrf_from, login, platform_db


@pytest.fixture
def other(client, parent):
    """Familie 2 mit Kind „Fremdkind“, Gerät „Fremdgerät“, Account „fremd“, Chat und Protokoll."""
    fid = add_family("Andere")
    with family_session(fid) as db:
        p = Parent(username="fremd", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(p)
        db.flush()
        db.add(Membership(parent_id=p.id, role=ROLE_OWNER))
        child = Child(name="Fremdkind")
        dev = Device(name="Fremdgerät", token_hash="f" * 64)
        db.add_all([child, dev])
        db.flush()
        db.add_all([DeviceUser(device_id=dev.id, username="fk", uid=1001, child_id=child.id),
                    Message(child_id=child.id, from_child=True, body="Geheimnis"),
                    BlockedEvent(child_id=child.id, device_id=dev.id, username="fk", at=utcnow(), key="exe:/x",
                                 name="Fremdprogramm"),
                    DayException(child_id=child.id, day=date.today() + timedelta(days=1), extra_minutes=30),
                    PairingCode(code_hash=token_hash("ABCDEFGH"), device_name="Fremd-PC",
                                expires_at=utcnow() + timedelta(minutes=10)),
                    PushSubscription(parent_id=p.id, endpoint="https://push.example/fremd", p256dh="x", auth="y")])
        db.commit()
        exc = db.scalar(select(DayException.id))
        return {"family": fid, "parent": p.id, "child": child.id, "device": dev.id, "exception": exc}


def test_lists_show_only_own_family(client, parent, other):
    login(client, parent)
    for path in ("/", "/children", "/devices", "/parents"):
        page = client.get(path).text
        assert "Fremdkind" not in page and "Fremdgerät" not in page and "fremd" not in page, path


def test_foreign_pages_are_not_found(client, parent, other):
    login(client, parent)
    c, d = other["child"], other["device"]
    for path in (f"/children/{c}", f"/children/{c}/zeiten", f"/children/{c}/internet", f"/children/{c}/programme",
                 f"/children/{c}/stats", f"/children/{c}/protokoll", f"/children/{c}/chat",
                 f"/children/{c}/chat/messages", f"/devices/{d}"):
        r = client.get(path)
        assert r.status_code == 404 and "Geheimnis" not in r.text and "Fremdprogramm" not in r.text, path


def test_foreign_actions_change_nothing(client, parent, other):
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    c, d = other["child"], other["device"]
    posts = [(f"/children/{c}/lock", {"duration": ""}), (f"/children/{c}/unlock", {}),
             (f"/children/{c}/credit", {"extra_minutes": "30", "day": date.today().isoformat()}),
             (f"/children/{c}/mode", {"weekdays_same": "1"}), (f"/children/{c}/web", {"web_mode": "blacklist"}),
             (f"/children/{c}/programs", {"program_mode": "enforce"}), (f"/children/{c}/delete", {}),
             (f"/children/{c}/exceptions/{other['exception']}/delete", {}),
             (f"/children/{c}/chat", {"body": "hallo"}),
             (f"/devices/{d}/mapping", {}), (f"/devices/{d}/alert", {"hours": "5"}), (f"/devices/{d}/revoke", {}),
             (f"/parents/{other['parent']}/delete", {}), (f"/parents/{other['parent']}/totp-reset", {})]
    for path, data in posts:
        r = client.post(path, data={"csrf_token": tok, **data})
        assert r.status_code == 404, (path, r.status_code)
    with family_session(other["family"]) as db:
        child = db.get(Child, c)
        dev = db.get(Device, d)
        assert child is not None and not child.locked and child.web_mode == "off" and child.program_mode == "off"
        assert dev.revoked_at is None and dev.alert_after_hours == 48 and dev.users[0].child_id == c
        assert db.get(Parent, other["parent"]) is not None and db.get(DayException, other["exception"]) is not None
        assert db.scalar(select(Message.body).where(Message.from_child.is_(False))) is None


def test_own_device_cannot_be_mapped_to_foreign_child(client, parent, other):
    login(client, parent)
    with SessionLocal() as db:
        dev = Device(name="PC", token_hash="e" * 64)
        db.add(dev)
        db.flush()
        db.add(DeviceUser(device_id=dev.id, username="kind", uid=1000))
        db.commit()
        dev_id, du_id = dev.id, dev.users[0].id
    tok = csrf_from(client.get(f"/devices/{dev_id}").text)
    client.post(f"/devices/{dev_id}/mapping", data={"csrf_token": tok, f"user_{du_id}": str(other["child"])})
    with SessionLocal() as db:
        assert db.get(DeviceUser, du_id).child_id is None


def test_pairing_code_of_other_family_creates_device_there(client, parent, other):
    from kidctl_common import protocol as p
    r = client.post(p.ENROLL_PATH, json={"code": "ABCD-EFGH", "hostname": "fremd-pc"})
    assert r.status_code == 200
    with platform_db() as db:   # Plattform: nur unverschlüsselte Spalten
        assert db.scalar(select(Device.family_id).where(Device.id == r.json()["device_id"])) == other["family"]
    login(client, parent)
    assert "Fremd-PC" not in client.get("/devices").text


def test_queries_without_context_fail_and_foreign_writes_are_rejected(client, parent, other):
    with PlainSession() as db, pytest.raises(TenantError):
        db.scalars(select(Child)).all()
    with SessionLocal() as db:
        assert db.get(Child, other["child"]) is None                       # fremde Zeile unsichtbar
        db.add(Child(name="Eingeschleust", family_id=other["family"]))
        with pytest.raises(TenantError):
            db.flush()
        db.rollback()
    for fid in (1, other["family"]):
        with family_session(fid) as db:
            assert db.scalar(select(Child.id).where(Child.name == "Eingeschleust")) is None
    with platform_db() as db, pytest.raises(Exception, match="ohne Familienkontext"):
        db.scalars(select(Child)).all()                                    # Plattform sieht keine Namen


def test_same_child_name_allowed_in_two_families(client, parent, other):
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    assert client.post("/children", data={"csrf_token": tok, "name": "Fremdkind"}).status_code in (200, 303)
    for fid in (1, other["family"]):
        with family_session(fid) as db:
            assert db.scalar(select(Child.id).where(Child.name == "Fremdkind")) is not None
    with platform_db() as db:   # verschieden verschlüsselt: gleicher Name, anderer Geheimtext
        from sqlalchemy import Text, type_coerce
        raw = db.scalars(select(type_coerce(Child.name, Text))).all()
        assert len(raw) == 2 and raw[0] != raw[1] and not any("Fremdkind" in r for r in raw)


def test_push_goes_only_to_own_family(client, parent, other, monkeypatch):
    sent = []
    monkeypatch.setattr(push.settings.__class__, "push_enabled", property(lambda self: True))
    monkeypatch.setattr(push, "_send_one", lambda sub, payload: sent.append(sub.endpoint) or True)
    with SessionLocal() as db:
        db.add(PushSubscription(parent_id=parent.id, endpoint="https://push.example/eigen", p256dh="x", auth="y"))
        db.commit()
    assert push.send_to_parents_sync("T", "B", family_id=1) == 1
    assert sent == ["https://push.example/eigen"]


def test_only_owner_manages_parents(client, parent):
    from conftest import add_parent
    papa = add_parent(username="papa")
    oma = add_parent(username="oma")
    login(client, papa)                                                   # Elternteil, nicht Inhaber
    page = client.get("/parents").text
    assert "Inhaber" in page and "Account hinzufügen" not in page and f"/parents/{oma.id}/delete" not in page
    tok = csrf_from(page)
    assert client.post(f"/parents/{oma.id}/delete", data={"csrf_token": tok}).status_code == 403
    assert client.post("/parents", data={"csrf_token": tok, "username": "x", "password": "y" * 12}).status_code == 403
    client.cookies.clear()
    login(client, parent)                                                 # Inhaber
    tok = csrf_from(client.get("/parents").text)
    assert client.post(f"/parents/{parent.id}/delete", data={"csrf_token": tok}).status_code == 400
    assert client.post(f"/parents/{oma.id}/delete", data={"csrf_token": tok}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.get(Parent, oma.id) is None and db.scalar(select(Membership).where(Membership.parent_id == oma.id)) is None
