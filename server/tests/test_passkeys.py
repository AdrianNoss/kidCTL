"""Passkeys als vollständiger Login (Eltern und Betreiber), frische Bestätigung, Entzug bei Wiederherstellung."""

import re

import pyotp
import pytest
from sqlalchemy import select

from conftest import PASSWORD, add_parent, csrf_from, login, next_code, platform_db, two_step_login
from softauthn import SoftAuthenticator
from app import passkeys
from app.models import AdminLog, Passkey, WebAuthnChallenge, utcnow

from test_accounts import _set, outbox  # noqa: F401 (Fixture)
from test_operators import admin_login, operator  # noqa: F401 (Fixture)


def _tok(client, page="/profile"):
    return csrf_from(client.get(page).text)


def _post_json(client, url, tok, **data):
    return client.post(url, data={"csrf_token": tok, **data}, headers={"x-csrf-token": tok})


def _confirm(client, account, base="/profile"):
    tok = _tok(client, base)
    return client.post(f"{base}/confirm", data={"csrf_token": tok, "current_password": PASSWORD,
                                                 "current_totp": next_code(account.totp_secret)},
                       follow_redirects=False)


def _register(client, auth, name="Laptop", base="/profile", **kw):
    tok = _tok(client, base)
    r = _post_json(client, f"{base}/passkeys/options", tok)
    if r.status_code != 200:
        return r
    return _post_json(client, f"{base}/passkeys", tok, credential=auth.create(r.json()["options"], **kw), name=name)


def _passkey_login(client, auth, base="/login", **kw):
    tok = csrf_from(client.get(base).text)
    r = _post_json(client, f"{base}/passkey/options", tok)
    assert r.status_code == 200, r.text
    return _post_json(client, f"{base}/passkey", tok, credential=auth.get(r.json()["options"], **kw))


def _setup(client, parent, backed_up=False):
    """Angemeldet, bestätigt, ein Passkey; danach abgemeldet."""
    assert login(client, parent).status_code == 303
    assert _confirm(client, parent).status_code == 303
    auth = SoftAuthenticator(backed_up=backed_up)
    assert _register(client, auth).json() == {"redirect": "/profile?msg=passkey_added#passkeys"}
    client.cookies.clear()
    return auth


def test_register_and_login_without_password_or_code(client, parent, outbox):  # noqa: F811
    _set(parent, email="mama@example.org", email_verified_at=utcnow())
    auth = _setup(client, parent)
    assert any("Neuer Passkey" in s and to == "mama@example.org" for to, s, _b in outbox)
    with platform_db() as db:
        pk = db.scalar(select(Passkey))
        assert (pk.kind, pk.account_id, pk.name, pk.transports) == ("parent", parent.id, "Laptop", "internal")
        assert db.scalar(select(WebAuthnChallenge)) is None                    # Challenge verbraucht
    page = client.get("/login").text
    assert 'autocomplete="username webauthn"' in page and "data-passkey-login" in page and " hidden>" in page
    r = _passkey_login(client, auth)
    assert r.status_code == 200 and r.json() == {"redirect": "/"}
    assert client.get("/", follow_redirects=False).status_code == 200          # angemeldet, ohne Passwort und Code
    profile = client.get("/profile").text
    assert "Laptop" in profile and "zuletzt benutzt" in profile


def test_freshness_required(client, parent, outbox):  # noqa: F811
    auth = _setup(client, parent)
    assert _passkey_login(client, auth).status_code == 200                      # Sitzung nicht bestätigt
    tok = _tok(client)
    assert _post_json(client, "/profile/passkeys/options", tok).status_code == 403
    with platform_db() as db:
        pk_id = db.scalar(select(Passkey.id))
    assert client.post(f"/profile/passkeys/{pk_id}/delete", data={"csrf_token": tok}).status_code == 400
    assert client.post("/profile/password", data={"csrf_token": tok, "password": "neues-passwort-123",
                                                  "repeat": "neues-passwort-123"}).status_code == 400
    page = client.post("/profile/totp", data={"csrf_token": tok}).text
    secret = re.search(r"Schlüssel <code>([A-Z2-7]+)</code>", page).group(1)
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = client.post("/profile/totp/confirm", data={"csrf_token": tok, "token": token,
                                                   "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 400                                                 # ohne Bestätigung kein neuer Authenticator
    # Umbenennen braucht keine Bestätigung
    assert client.post(f"/profile/passkeys/{pk_id}/rename", data={"csrf_token": tok, "name": "Handy"},
                       follow_redirects=False).status_code == 303
    # Bestätigung per Passkey, dann Löschen
    r = _post_json(client, "/profile/confirm/passkey/options", tok)
    r = _post_json(client, "/profile/confirm/passkey", tok, credential=auth.get(r.json()["options"]))
    assert r.json() == {"redirect": "/profile?msg=confirmed#passkeys"}
    assert client.post(f"/profile/passkeys/{pk_id}/delete", data={"csrf_token": tok},
                       follow_redirects=False).status_code == 303
    with platform_db() as db:
        assert db.scalar(select(Passkey)) is None


@pytest.mark.parametrize("bad", [{"origin": "https://evil.example"}, {"rp_id": "evil.example"}])
def test_wrong_origin_or_rp_id(client, parent, outbox, bad):  # noqa: F811
    auth = _setup(client, parent)
    assert _passkey_login(client, auth, **bad).status_code == 400
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert login(client, parent).status_code == 303
    assert _confirm(client, parent).status_code == 303
    assert _register(client, SoftAuthenticator(), **bad).status_code == 400


def test_challenge_is_single_use(client, parent, outbox):  # noqa: F811
    auth = _setup(client, parent)
    tok = csrf_from(client.get("/login").text)
    options = _post_json(client, "/login/passkey/options", tok).json()["options"]
    cred = auth.get(options)
    assert _post_json(client, "/login/passkey", tok, credential=cred).status_code == 200
    client.cookies.delete("kidctl_session")
    assert _post_json(client, "/login/passkey", tok, credential=cred).status_code == 400   # gleiche Challenge


def test_sign_count_regression(client, parent, outbox):  # noqa: F811
    auth = _setup(client, parent)
    assert _passkey_login(client, auth, counter=5).status_code == 200
    client.cookies.clear()
    assert _passkey_login(client, auth, counter=3).status_code == 400            # Zähler zurück: Klon?
    synced = _setup(client, add_parent(username="papa", email="papa@example.org"), backed_up=True)
    for _ in range(2):                                                         # synchronisiert: Zähler bleibt 0
        client.cookies.clear()
        assert _passkey_login(client, synced, counter=0).status_code == 200


def test_recovery_removes_passkeys(client, parent, outbox):  # noqa: F811
    _setup(client, parent)
    papa = add_parent(username="papa", email="papa@example.org")
    _setup(client, papa)
    with platform_db() as db:
        assert db.scalar(select(Passkey).where(Passkey.account_id == papa.id)) is not None
    assert login(client, parent).status_code == 303                            # Inhaber setzt Authenticator von Papa zurück
    tok = _tok(client, "/parents")
    assert client.post(f"/parents/{papa.id}/totp-reset", data={"csrf_token": tok}).status_code == 200
    with platform_db() as db:
        assert db.scalar(select(Passkey).where(Passkey.account_id == papa.id)) is None
        assert db.scalar(select(Passkey).where(Passkey.account_id == parent.id)) is not None
        assert passkeys.remove_all(db, "parent", parent.id) == 1               # wie „Passwort vergessen“
        db.commit()


def test_password_reset_removes_passkeys(client, parent, outbox):  # noqa: F811
    _set(parent, email="mama@example.org", email_verified_at=utcnow())
    _setup(client, parent)
    tok = csrf_from(client.get("/reset").text)
    from app import accounts
    with platform_db() as db:
        accounts.start_password_reset(db, "mama@example.org")
    link = re.search(r"http://testserver(/reset/\S+)", outbox[-1][2]).group(1)
    tok = csrf_from(client.get(link).text)
    r = client.post(link, data={"csrf_token": tok, "password": "ganz-neues-passwort", "repeat": "ganz-neues-passwort"},
                    follow_redirects=False)
    assert r.status_code == 303
    with platform_db() as db:
        assert db.scalar(select(Passkey)) is None
    assert any("Passkeys entfernt" in s for _to, s, _b in outbox)


def test_operator_passkeys(client, operator, parent, outbox):  # noqa: F811
    admin_login(client, operator)
    assert _confirm(client, operator, "/admin/profile").status_code == 303
    auth = SoftAuthenticator()
    assert _register(client, auth, "Yubikey", base="/admin/profile").json()["redirect"].startswith("/admin/profile")
    with platform_db() as db:
        assert db.scalar(select(AdminLog).where(AdminLog.object == "operator", AdminLog.field == "passkey")) is not None
    client.cookies.clear()
    r = _passkey_login(client, auth, base="/admin/login")
    assert r.json() == {"redirect": "/admin"}
    assert client.get("/admin", follow_redirects=False).status_code == 200
    # Betreiber-Passkey taugt nicht für die Eltern-Anmeldung und umgekehrt
    client.cookies.clear()
    assert _passkey_login(client, auth).status_code == 400
    parent_auth = _setup(client, parent)
    assert _passkey_login(client, parent_auth, base="/admin/login").status_code == 400


def test_classic_login_unchanged(client, parent):
    assert two_step_login(client, "/login", "mama", PASSWORD,
                          pyotp.TOTP(parent.totp_secret).now()).headers["location"] == "/"
