"""„Jetzt sperren“: Sperre/Freigabe im WebUI, Verteilung per Policy, Ablauf, Migration."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from app import lock as lk
from app import migrate
from app.db import SessionLocal
from app.models import Child, utcnow

from test_policy import _policy, _setup, device


def test_lock_and_unlock_pushes_policy(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        assert _policy(ws).children[0].locked is False
        r = client.post(f"/children/{cid}/lock", data={"csrf_token": tok, "duration": "", "back": "dashboard"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        pol = _policy(ws).children[0]
        assert pol.locked is True and pol.locked_until is None
        dash = client.get("/").text
        assert "gesperrt" in dash and ">Freigeben<" in dash

        client.post(f"/children/{cid}/unlock", data={"csrf_token": tok})
        pol = _policy(ws).children[0]
        assert pol.locked is False and pol.locked_until is None
        assert "Jetzt sperren" in client.get(f"/children/{cid}").text


def test_timed_lock_and_expiry(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        _policy(ws)
        client.post(f"/children/{cid}/lock", data={"csrf_token": tok, "duration": "30"})
        pol = _policy(ws).children[0]
        assert pol.locked and pol.locked_until.tzinfo is not None
        left = pol.locked_until - datetime.now(timezone.utc)
        assert timedelta(minutes=29) < left <= timedelta(minutes=30)
        assert "gesperrt bis" in client.get(f"/children/{cid}").text
    with SessionLocal() as db:
        child = db.get(Child, cid)
        child.locked_until = utcnow() - timedelta(seconds=1)   # abgelaufen
        db.commit()
        assert not lk.is_locked(child)
    assert "nicht gesperrt" in client.get(f"/children/{cid}").text


def test_invalid_duration_rejected(client, parent):
    cid, tok = _setup(client, parent)
    assert client.post(f"/children/{cid}/lock", data={"csrf_token": tok, "duration": "999"}).status_code == 400


def test_lock_requires_csrf(client, parent):
    cid, _ = _setup(client, parent)
    assert client.post(f"/children/{cid}/lock", data={"duration": ""}).status_code in (400, 403)


def test_until_tomorrow_is_next_local_midnight():
    # 20:00 UTC = 22:00 Berlin (Sommerzeit) -> 00:00 Berlin = 22:00 UTC am selben Tag
    assert lk.until_for("tomorrow", datetime(2026, 10, 15, 20, 0)) == datetime(2026, 10, 15, 22, 0)
    # 23:30 UTC = 01:30 Berlin am 16. -> Mitternacht danach
    assert lk.until_for("tomorrow", datetime(2026, 10, 15, 23, 30)) == datetime(2026, 10, 16, 22, 0)
    # Winterzeit (UTC+1)
    assert lk.until_for("tomorrow", datetime(2026, 12, 1, 12, 0)) == datetime(2026, 12, 1, 23, 0)


def test_child_lock_columns_migrated(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, name VARCHAR(64))"))
        c.execute(text("INSERT INTO child (name) VALUES ('Max')"))
    migrate.run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT locked, locked_until FROM child")).one() == (0, None)
