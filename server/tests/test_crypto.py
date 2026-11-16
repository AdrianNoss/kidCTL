"""Verschlüsselung je Familie: Kern (Schlüssel, Feldtypen, Kontextbindung)."""

import pytest

from app import crypto
from app.models import Family
from conftest import add_family, platform_db


def test_each_family_gets_its_own_wrapped_key(client):
    other = add_family("B")
    with platform_db() as db:
        a, b = db.get(Family, 1), db.get(Family, other)
        assert a.wrapped_key and b.wrapped_key and a.wrapped_key != b.wrapped_key
        crypto.forget(1)                                    # aus der Datenbank nachladen
        assert crypto.unwrap_family_key(1, a.wrapped_key) == crypto._key(1)
        with pytest.raises(crypto.CryptoError):              # an die Familie gebunden
            crypto.unwrap_family_key(other, a.wrapped_key)


def test_sealed_random_matchable_deterministic_and_bound(client):
    other = add_family("B")
    with crypto.family(1):
        s1, s2 = crypto.seal("Geheim", "t.c"), crypto.seal("Geheim", "t.c")
        m1, m2 = crypto.match("domain:youtube.com", "t.k"), crypto.match("domain:youtube.com", "t.k")
        assert s1 != s2 and m1 == m2 and "youtube" not in m1
        assert crypto.open_(s1, "t.c") == "Geheim" and crypto.open_(m1, "t.k") == "domain:youtube.com"
        with pytest.raises(crypto.DecryptError):
            crypto.open_(s1, "t.anders")                    # andere Spalte
    with crypto.family(other):
        assert crypto.match("domain:youtube.com", "t.k") != m1   # andere Familie, anderer Geheimtext
        with pytest.raises(crypto.DecryptError):
            crypto.open_(s1, "t.c")                         # fremde Familie
    with pytest.raises(crypto.CryptoError):
        crypto.seal("x", "t.c")                             # kein Kontext


def test_email_index_ignores_case(client):
    assert crypto.email_index("Mama@Example.org") == crypto.email_index("mama@example.org ")
    assert crypto.email_index("") == "" and len(crypto.email_index("a@b.de")) == 64


def test_master_key_required_outside_dev(monkeypatch):
    from app import config
    monkeypatch.delenv("KIDCTL_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="KIDCTL_MASTER_KEY"):
        config._master_key(dev=False)
    assert config._master_key(dev=True) == config.DEV_MASTER_KEY
    assert len(crypto.new_master_key()) == 43


def test_email_is_stored_encrypted_and_found_by_index(client, parent):
    from sqlalchemy import Text, select, type_coerce

    from app import accounts
    from app.models import Parent, utcnow
    from conftest import SessionLocal
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        p.email, p.email_verified_at = "Mama@Example.org", utcnow()
        db.commit()
    with platform_db() as db:
        raw, idx = db.execute(select(type_coerce(Parent.email, Text), Parent.email_index)).one()
        assert "Mama" not in raw and raw.startswith("g1:") and idx == crypto.email_index("mama@example.org")
        found = accounts.find_by_login(db, "MAMA@example.org")         # Plattform -> Familie des Accounts
        assert found.id == parent.id and found.email == "Mama@Example.org"


def test_delete_family_shreds_key_first_and_removes_everything(client, parent):
    from sqlalchemy import Text, select, type_coerce

    from app.models import Child, Membership, Message, Parent, PushSubscription
    from app.tenant import delete_family, family_session
    fid = add_family("Weg")
    with family_session(fid) as db:
        p = Parent(username="weg", password_hash="h", totp_secret="T", email="weg@example.org")
        db.add(p)
        db.flush()
        db.add(Membership(parent_id=p.id, role="owner"))
        c = Child(name="Geheimkind")
        db.add(c)
        db.flush()
        db.add_all([Message(child_id=c.id, from_child=True, body="streng geheim"),
                    PushSubscription(parent_id=p.id, endpoint="https://push.example/weg", p256dh="x", auth="y")])
        db.commit()
        pid = p.id
    with platform_db() as db:   # „altes Backup“: Geheimtexte vor dem Löschen
        backup = db.scalars(select(type_coerce(Message.body, Text)).where(Message.family_id == fid)).all()
        assert delete_family(db, fid) == {"parents": 1, "children": 1, "devices": 0}
        assert db.get(Family, fid) is None and db.scalar(select(Parent.id).where(Parent.id == pid)) is None
        assert db.scalars(select(Child.id).where(Child.family_id == fid)).all() == []
        assert db.scalars(select(PushSubscription.id).where(PushSubscription.parent_id == pid)).all() == []
    with pytest.raises(crypto.CryptoError):        # Schlüssel weg: auch das Backup ist unlesbar
        crypto.open_(backup[0], "message.body", family_id=fid)
    with platform_db() as db:                      # andere Familie unberührt
        assert db.get(Family, 1).wrapped_key and db.scalar(select(Parent.id).where(Parent.id == parent.id))
