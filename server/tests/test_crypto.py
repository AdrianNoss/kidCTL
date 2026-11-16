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
