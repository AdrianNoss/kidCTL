"""Etappe 4.5: Export/Import je Familie, Familie löschen, Backup im SaaS nur beim Betreiber."""

import io
import json
import zipfile
from datetime import date, time

import pyotp
import pytest
from sqlalchemy import select

from app import web
from app.config import Settings
from app.models import (
    ROLE_OWNER, Child, Device, DeviceUser, Family, Membership, Message, Parent, UsageRecord, WeekdayRule,
)
from app.security import hash_password, new_totp_secret
from app.tenant import family_session
from conftest import two_step_login, PASSWORD, SessionLocal, add_family, add_parent, csrf_from, login, platform_db


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _fill(parent_id):
    with SessionLocal() as db:
        kid = Child(name="Lena")
        db.add(kid)
        db.flush()
        db.add(WeekdayRule(child_id=kid.id, weekday=0, quota_minutes=90, window_start=time(8), window_end=time(20)))
        dev = Device(name="Laptop", token_hash="t" * 64)
        db.add(dev)
        db.flush()
        db.add(DeviceUser(device_id=dev.id, username="lena", uid=1001, full_name="Lena M.", child_id=kid.id))
        db.add(UsageRecord(device_id=dev.id, username="lena", day=date(2026, 11, 1), child_id=kid.id, seconds=3600))
        db.add(Message(child_id=kid.id, from_child=False, sender_parent_id=parent_id, body="Abendessen!"))
        db.commit()


def _owner_of_new_family(name="Neu"):
    fid = add_family(name)
    with family_session(fid) as db:
        p = Parent(username=f"inhaber-{fid}", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(p)
        db.flush()
        db.add(Membership(parent_id=p.id, role=ROLE_OWNER))
        db.commit()
        return fid, p


def _export(client, password=PASSWORD):
    tok = csrf_from(client.get("/settings").text)
    return client.post("/settings/export", data={"csrf_token": tok, "current_password": password})


def _import(client, data, password=PASSWORD):
    tok = csrf_from(client.get("/settings").text)
    return client.post("/settings/import", data={"csrf_token": tok, "current_password": password},
                       files={"file": ("export.zip", data, "application/zip")}, follow_redirects=False)


def test_export_import_roundtrip_into_other_family(client, parent):
    _fill(parent.id)
    login(client, parent)
    assert _export(client, "falsch").status_code == 400
    r = _export(client)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "kidctl-family-export" and manifest["version"] == 1 and manifest["tables"]["child"] == 1
    assert "membership.json" not in zf.namelist() and "Abendessen!" in zf.read("message.json").decode()   # lesbar
    assert _import(client, r.content).status_code == 400                        # Familie nicht leer

    fid, owner = _owner_of_new_family()
    client.cookies.clear()
    assert two_step_login(client, "/login", owner.username, PASSWORD,
                          pyotp.TOTP(owner.totp_secret).now()).status_code == 303
    page = client.get("/settings").text
    assert 'action="/settings/import"' in page
    r2 = _import(client, r.content)
    assert r2.status_code == 303
    with family_session(fid) as db:
        kid = db.scalar(select(Child))
        assert kid.name == "Lena" and db.scalar(select(WeekdayRule.quota_minutes).where(WeekdayRule.child_id == kid.id)) == 90
        dev = db.scalar(select(Device))
        assert dev.name == "Laptop" and dev.revoked_at is not None and dev.token_hash != "t" * 64   # neu koppeln
        du = db.scalar(select(DeviceUser))
        assert du.device_id == dev.id and du.child_id == kid.id and du.full_name == "Lena M."
        assert db.scalar(select(UsageRecord.seconds).where(UsageRecord.child_id == kid.id)) == 3600
        msg = db.scalar(select(Message))
        assert msg.body == "Abendessen!" and msg.child_id == kid.id and msg.sender_parent_id is None
    with SessionLocal() as db:                                                  # Quelle unverändert
        assert db.scalar(select(Device.token_hash)) == "t" * 64
    assert 'action="/settings/import"' not in client.get("/settings").text     # jetzt nicht mehr leer


def test_import_rejects_bad_files(client, parent):
    login(client, parent)
    assert "keine KidCTL-Export-Datei" in _import(client, b"kein zip").text
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "kidctl-family-export", "version": 99}))
    assert "neueren KidCTL-Version" in _import(client, buf.getvalue()).text
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "kidctl-family-export", "version": 1}))
        zf.writestr("child.json", json.dumps([{"id": 1, "name": None}]))
    assert "beschädigt" in _import(client, buf.getvalue()).text
    with SessionLocal() as db:
        assert db.scalar(select(Child)) is None                                 # nichts halb übernommen


def test_only_owner_exports(client, parent):
    papa = add_parent(username="papa")
    login(client, papa)
    assert "nur der Inhaber" in client.get("/settings").text
    tok = csrf_from(client.get("/settings").text)
    assert client.post("/settings/export", data={"csrf_token": tok, "current_password": PASSWORD}).status_code == 403


def test_saas_hides_database_backup(client, parent, saas):
    login(client, parent)
    page = client.get("/settings").text
    assert "Datenbank-Backup" not in page and "Familie löschen" in page
    tok = csrf_from(page)
    assert client.post("/settings/backup", data={"csrf_token": tok}).status_code == 404
    assert client.post("/settings/backup/download", data={"csrf_token": tok, "name": "x",
                                                         "current_password": PASSWORD}).status_code == 404


def test_owner_deletes_family(client, parent, saas, monkeypatch):
    from app import policy
    pushed = []

    async def fake_push(db, ids):
        pushed.append((set(ids), db.get(Family, 1).paused_at is not None))
    monkeypatch.setattr(policy, "push_policies", fake_push)
    _fill(parent.id)
    other, _ = _owner_of_new_family("Andere")
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    r = client.post("/settings/delete-family", data={"csrf_token": tok, "current_password": PASSWORD, "confirm": "nein"})
    assert r.status_code == 400
    r = client.post("/settings/delete-family", data={"csrf_token": tok, "current_password": PASSWORD, "confirm": "LÖSCHEN"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?msg=family_deleted"
    assert pushed and pushed[0][1] and len(pushed[0][0]) == 1                   # vorher Ruhemodus an Agenten
    with platform_db() as db:
        assert db.get(Family, 1) is None and db.get(Parent, parent.id) is None and db.get(Family, other) is not None
    assert "wurden gelöscht" in client.get("/login?msg=family_deleted").text


def test_single_mode_cannot_delete_family(client, parent):
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    assert client.post("/settings/delete-family", data={"csrf_token": tok, "current_password": PASSWORD,
                                                       "confirm": "LÖSCHEN"}).status_code == 404
