"""Login in zwei Schritten (Eltern und Betreiber): Reihenfolge, keine Kontoauskunft, Tickets, Sperren."""

import re
from datetime import timedelta

import pyotp
from sqlalchemy import select, update

from conftest import PASSWORD, csrf_from, platform_db, two_step_login
from app import loginticket, security
from app.models import LoginTicket, utcnow

from test_operators import operator  # noqa: F401 (Fixture)


def _step1(client, username, password, base="/login"):
    page = client.get(base)
    return client.post(base, data={"csrf_token": csrf_from(page.text), "username": username, "password": password},
                       follow_redirects=False)


def _step2(client, code, base="/login"):
    page = client.get(base + "/code", follow_redirects=False)
    if page.status_code != 200:
        return page
    return client.post(base + "/code", data={"csrf_token": csrf_from(page.text), "totp": code},
                       follow_redirects=False)


def _neutral(html):
    return re.sub(r'name="csrf_token" value="[^"]*"', "", html)


def test_password_first_then_code(client, parent):
    r = _step1(client, "mama", PASSWORD)
    assert r.status_code == 303 and r.headers["location"] == "/login/code"
    cookie = r.headers["set-cookie"].lower()
    assert "kidctl_login=" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "max-age=300" in cookie
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"   # noch nicht angemeldet
    page = client.get("/login/code").text
    assert 'name="totp"' in page and 'name="password"' not in page and 'method="post" action="/login/code"' in page
    r = _step2(client, pyotp.TOTP(parent.totp_secret).now())
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/", follow_redirects=False).status_code == 200
    with platform_db() as db:
        assert db.scalar(select(LoginTicket)) is None                                    # Ticket verbraucht


def test_login_page_works_without_javascript(client):
    page = client.get("/login").text
    assert 'method="post" action="/login"' in page and 'name="totp"' not in page


def test_unknown_account_and_wrong_password_look_the_same(client, parent, monkeypatch):
    checks = []
    real_verify, real_dummy = security.verify_password, security.dummy_verify
    monkeypatch.setattr("app.routes.auth.verify_password", lambda *a: checks.append("v") or real_verify(*a))
    monkeypatch.setattr("app.routes.auth.dummy_verify", lambda: checks.append("d") or real_dummy())
    wrong = _step1(client, "mama", "falsch-falsch-falsch")
    unknown = _step1(client, "niemand@example.org", "falsch-falsch-falsch")
    assert wrong.status_code == unknown.status_code == 401
    assert _neutral(wrong.text).replace("mama", "X") == _neutral(unknown.text).replace("niemand@example.org", "X")
    assert "kidctl_login" not in wrong.headers.get("set-cookie", "")
    assert checks == ["v", "d"]                     # je genau eine Hash-Prüfung: vergleichbare Laufzeit


def test_ticket_is_single_use_and_expires(client, parent):
    _step1(client, "mama", PASSWORD)
    token = client.cookies.get("kidctl_login")
    _step2(client, pyotp.TOTP(parent.totp_secret).now())
    client.cookies.clear()
    client.cookies.set("kidctl_login", token)                                            # altes Ticket erneut
    r = client.get("/login/code", follow_redirects=False)
    assert r.headers["location"] == "/login?msg=expired"
    assert "abgelaufen" in client.get("/login?msg=expired").text

    client.cookies.clear()
    _step1(client, "mama", PASSWORD)
    with platform_db() as db:
        db.execute(update(LoginTicket).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert _step2(client, pyotp.TOTP(parent.totp_secret).now()).headers["location"] == "/login?msg=expired"


def test_ticket_bound_to_kind(client, parent, operator):  # noqa: F811
    _step1(client, "mama", PASSWORD)
    token = client.cookies.get("kidctl_login")
    client.cookies.clear()
    client.cookies.set("kidctl_admin_login", token, path="/admin")                     # Eltern-Ticket im Betreiber-Login
    assert client.get("/admin/login/code", follow_redirects=False).headers["location"] == "/admin/login?msg=expired"
    with platform_db() as db:
        t = db.scalar(select(LoginTicket))
        assert t.kind == "parent" and len(t.token_hash) == 64 and t.token_hash != token   # nur der Hash liegt in der DB


def test_five_wrong_codes_end_ticket_and_lock_totp(client, parent):
    _step1(client, "mama", PASSWORD)
    for _ in range(loginticket.MAX_ATTEMPTS - 1):
        assert _step2(client, "000000").status_code == 401
    r = _step2(client, "000000")
    assert r.status_code == 303 and r.headers["location"] == "/login?msg=expired"   # Ticket ungültig
    r = _step1(client, "mama", PASSWORD)                                              # Authenticator gesperrt
    assert r.status_code == 429 and "Zu viele" in r.text


def test_code_without_ticket(client):
    assert client.get("/login/code", follow_redirects=False).headers["location"] == "/login?msg=expired"


def test_operator_two_steps(client, operator):  # noqa: F811
    r = _step1(client, operator.username, PASSWORD, "/admin/login")
    assert r.status_code == 303 and r.headers["location"] == "/admin/login/code"
    assert "path=/admin" in r.headers["set-cookie"].lower()
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    r = _step2(client, pyotp.TOTP(operator.totp_secret).now(), "/admin/login")
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    client.cookies.clear()
    wrong = _step1(client, operator.username, "falsch-falsch-falsch", "/admin/login")
    unknown = _step1(client, "niemand", "falsch-falsch-falsch", "/admin/login")
    assert wrong.status_code == unknown.status_code == 401


def test_helper_full_login(client, parent):
    assert two_step_login(client, "/login", "mama", PASSWORD,
                          pyotp.TOTP(parent.totp_secret).now()).headers["location"] == "/"


def _parallel(n, fn):
    import threading
    barrier, results = threading.Barrier(n), []

    def work():
        with platform_db() as db:
            barrier.wait()
            results.append(fn(db))
    threads = [threading.Thread(target=work) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_code_attempts_counted_atomically(client, parent):
    """Gleichzeitige Versuche: je Ticket und je Konto höchstens ``MAX_ATTEMPTS`` bzw. ``login_max_failures``."""
    from app.config import settings
    with platform_db() as db:
        loginticket.create(db, "parent", parent.id)
        tid = db.scalar(select(LoginTicket.id))

    def claim(db):
        return loginticket.claim(db, db.get(LoginTicket, tid))
    assert sum(_parallel(20, claim)) == loginticket.MAX_ATTEMPTS

    def begin(db):
        return security.begin_totp_attempt(db, "1.2.3.4", "mama") is not None
    assert sum(_parallel(20, begin)) == settings.login_max_failures
    with platform_db() as db:
        assert security.totp_locked(db, "mama")
