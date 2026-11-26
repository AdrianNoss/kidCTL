"""Nintendo-Switch-Spielzeit (experimentell): Abbildung, Abruf und Fehlerfälle, Einrichtung, Anzeige.

Ohne echte Nintendo-Aufrufe: Die Bibliothek wird durch Attrappen ersetzt (der Hintergrundabruf ist in conftest aus).
"""

import asyncio
import io
import json
import logging
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pynintendoauth.exceptions import HttpException, InvalidSessionTokenException
from sqlalchemy import select, text

from app import nintendo, retention
from app import schedule as sch
from app.config import settings
from app.family_export import export_zip
from app.models import Child, NintendoConsole, NintendoLink, NintendoPlayer, NintendoUsage, utcnow
from conftest import TEST_FAMILY, SessionLocal, csrf_from, login

TOKEN = "geheimes-session-token-123"
SYNC = 1_764_150_000   # Unix-Zeit


def _app(name, minutes):
    return SimpleNamespace(application=SimpleNamespace(name=name), playing_time=minutes)


def _device(device_id="dev1", *, limit=120, extra=None, today=45, remaining=75, players=None, summaries=True,
            name="Wohnzimmer"):
    if players is None:
        players = [SimpleNamespace(player_id="p1", nickname="Max", playing_time=40,
                                   apps=[_app("Zelda", 30), _app("Mario Kart", 10)]),
                   SimpleNamespace(player_id="p2", nickname="Gast", playing_time=5, apps=[])]
    return SimpleNamespace(device_id=device_id, name=name, model="Switch", limit_time=limit, extra_playing_time=extra,
                           today_playing_time=today, today_time_remaining=remaining,
                           daily_summaries=[{"date": "x"}] if summaries else [],
                           last_sync=SYNC, players=players)


def _run(coro):
    return asyncio.run(coro)


def _linked(next_poll=None):
    with SessionLocal() as db:
        db.add(NintendoLink(session_token=TOKEN, status=nintendo.OK, next_poll_at=next_poll))
        db.commit()


def _fake_fetch(monkeypatch, result):
    """``_fetch`` ersetzen: Liste von Geräten (Erfolg) oder Ausnahme."""
    async def fetch(family_id, token, session):
        assert token == TOKEN
        if isinstance(result, Exception):
            raise result
        devices, failed = result
        return [nintendo.snapshot(d) for d in devices], failed
    monkeypatch.setattr(nintendo, "_fetch", fetch)


def _child(client, parent, name="Max") -> tuple[int, str]:
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": name, "csrf_token": tok})
    with SessionLocal() as db:
        return db.scalar(select(Child.id).where(Child.name == name)), tok


# ---------- Abbildung ----------

def test_snapshot_maps_device():
    c = nintendo.snapshot(_device())
    assert (c.key, c.name, c.model) == ("dev1", "Wohnzimmer", "Switch")
    assert (c.used_minutes, c.limit_minutes, c.remaining_minutes) == (45, 120, 75)
    assert c.last_sync == datetime.fromtimestamp(SYNC, timezone.utc).replace(tzinfo=None)
    assert [(p.key, p.nickname, p.minutes) for p in c.players] == [("p1", "Max", 40), ("p2", "Gast", 5)]
    assert c.players[0].apps == [("Zelda", 30), ("Mario Kart", 10)]


def test_snapshot_unlimited_has_no_remaining():
    # Die Bibliothek liefert bei „kein Limit“ die Minuten bis Mitternacht – kidCTL zeigt „unbegrenzt“ (None)
    assert (nintendo.snapshot(_device(limit=-1, remaining=600)).limit_minutes,
            nintendo.snapshot(_device(limit=-1, remaining=600)).remaining_minutes) == (None, None)
    c = nintendo.snapshot(_device(limit=60, extra=-1, remaining=600))
    assert c.remaining_minutes is None and c.limit_minutes is None


def test_incomplete_new_console_is_tolerated(client, parent, monkeypatch):
    dev = SimpleNamespace(device_id="neu", name=None, limit_time=None, daily_summaries=[],
                          last_sync="kaputt", players=[SimpleNamespace(player_id=None), SimpleNamespace(player_id="p9")])
    c = nintendo.snapshot(dev)
    assert (c.used_minutes, c.limit_minutes, c.remaining_minutes, c.last_sync) == (None, None, None, None)
    assert [(p.key, p.minutes) for p in c.players] == [("p9", None)]
    _linked()
    _fake_fetch(monkeypatch, ([dev], set()))
    assert _run(nintendo.poll_family(TEST_FAMILY, None)) == nintendo.OK
    with SessionLocal() as db:
        con = db.scalar(select(NintendoConsole))
        assert con.used_minutes is None and len(con.players) == 1
        assert db.scalar(select(NintendoUsage)) is None       # ohne Tageswert kein Eintrag


# ---------- Abruf ----------

def test_poll_success_stores_console_players_usage(client, parent, monkeypatch, caplog):
    _linked()
    _fake_fetch(monkeypatch, ([_device()], set()))
    caplog.set_level(logging.DEBUG)
    assert _run(nintendo.poll_family(TEST_FAMILY, None)) == nintendo.OK
    with SessionLocal() as db:
        con = db.scalar(select(NintendoConsole))
        assert (con.name, con.used_minutes, con.limit_minutes, con.remaining_minutes, con.stale) == \
            ("Wohnzimmer", 45, 120, 75, False)
        assert con.day == sch.today_local()
        assert sorted(p.nickname for p in con.players) == ["Gast", "Max"]
        assert all(p.child_id is None for p in con.players)       # nicht zugeordnet, aber nicht verworfen
        usage = {u.minutes: u for u in db.scalars(select(NintendoUsage))}
        assert set(usage) == {40, 5} and json.loads(usage[40].apps) == [["Zelda", 30], ["Mario Kart", 10]]
        row = db.scalar(select(NintendoLink))
        assert row.failures == 0 and row.last_ok_at and row.next_poll_at > utcnow()
    assert "neue Spielerprofile ohne Zuordnung" in caplog.text
    assert TOKEN not in caplog.text and "Max" not in caplog.text


def test_poll_auth_error_requires_relogin_without_retry(client, parent, monkeypatch, caplog):
    _linked()
    _fake_fetch(monkeypatch, ([_device()], set()))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    _fake_fetch(monkeypatch, InvalidSessionTokenException(400, "invalid_grant"))
    caplog.set_level(logging.DEBUG)
    assert _run(nintendo.poll_family(TEST_FAMILY, None)) == nintendo.REAUTH
    with SessionLocal() as db:
        row = db.scalar(select(NintendoLink))
        assert row.status == nintendo.REAUTH and row.next_poll_at is None
        assert db.scalar(select(NintendoConsole)).stale
    with SessionLocal() as db:
        db.scalar(select(NintendoLink)).next_poll_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert TEST_FAMILY not in nintendo.due_families()          # keine Retry-Schleife
    assert TOKEN not in caplog.text


def test_poll_network_error_keeps_values_and_backs_off(client, parent, monkeypatch):
    _linked()
    _fake_fetch(monkeypatch, ([_device()], set()))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    _fake_fetch(monkeypatch, HttpException(503, "unavailable"))
    for expected in (1, 2):
        before = utcnow()
        assert _run(nintendo.poll_family(TEST_FAMILY, None)) == nintendo.ERROR
        with SessionLocal() as db:
            row = db.scalar(select(NintendoLink))
            assert (row.failures, row.error) == (expected, "http_503")
            wait = (row.next_poll_at - before).total_seconds()
            assert nintendo.backoff(expected) - 2 <= wait <= nintendo.backoff(expected) + 2
            con = db.scalar(select(NintendoConsole))
            assert con.stale and con.used_minutes == 45      # letzter Wert bleibt
    assert nintendo.backoff(2) == 2 * settings.nintendo_interval and nintendo.backoff(10) == nintendo.MAX_BACKOFF
    _fake_fetch(monkeypatch, ([_device(today=50)], set()))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    with SessionLocal() as db:
        row, con = db.scalar(select(NintendoLink)), db.scalar(select(NintendoConsole))
        assert (row.status, row.failures, con.stale, con.used_minutes) == (nintendo.OK, 0, False, 50)


def test_poll_one_console_failing_marks_only_it_stale(client, parent, monkeypatch):
    _linked()
    _fake_fetch(monkeypatch, ([_device("a", name="A"), _device("b", name="B")], set()))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    _fake_fetch(monkeypatch, ([_device("a", name="A", today=90), _device("b", name="B", today=99)], {"b"}))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    with SessionLocal() as db:
        cons = {c.name: c for c in db.scalars(select(NintendoConsole))}
        assert (cons["A"].stale, cons["A"].used_minutes) == (False, 90)
        assert (cons["B"].stale, cons["B"].used_minutes) == (True, 45)


def test_fetch_uses_library_and_passes_auth_errors(monkeypatch):
    """``_fetch`` gegen Attrappen der Bibliothek: Einzelfehler je Konsole, Auth-Fehler bricht ab."""
    import pynintendoparental

    class Auth:
        def __init__(self, session_token=None, client_session=None):
            assert session_token == TOKEN

        async def async_complete_login(self, response_token=None, use_session_token=False):
            assert use_session_token

    class Dev(SimpleNamespace):
        async def update(self, monthly_summary=True):
            assert monthly_summary is False
            if self.fail:
                raise self.fail

    devices = {"ok": Dev(**vars(_device("ok")), fail=None), "bad": Dev(**vars(_device("bad")), fail=RuntimeError("x"))}

    class Parental:
        def __init__(self, auth, tz, lang):
            pass

        async def discover_devices(self):
            return devices

    monkeypatch.setattr(pynintendoparental, "Authenticator", Auth)
    monkeypatch.setattr(pynintendoparental, "NintendoParental", Parental)
    nintendo._clients.clear()
    consoles, failed = _run(nintendo._fetch(99, TOKEN, None))
    assert {c.key for c in consoles} == {"ok", "bad"} and failed == {"bad"}
    devices["ok"].fail = InvalidSessionTokenException(400, "invalid_grant")
    with pytest.raises(InvalidSessionTokenException):
        _run(nintendo._fetch(99, TOKEN, None))
    nintendo._clients.clear()


# ---------- Einrichtung (WebUI) ----------

def test_setup_flow_stores_token_encrypted(client, parent, monkeypatch):
    import pynintendoparental
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    assert "Verbindung einrichten" in client.get("/settings").text
    r = client.post("/settings/nintendo/start", data={"csrf_token": tok}, follow_redirects=True)
    assert "https://accounts.nintendo.com/connect/1.0.0/authorize?" in r.text
    with SessionLocal() as db:
        verifier = db.scalar(select(NintendoLink)).pending_verifier
    assert verifier

    r = client.post("/settings/nintendo/link", data={"csrf_token": tok, "url": "https://accounts.nintendo.com/x"})
    assert r.status_code == 400 and "nicht der kopierte Link" in r.text

    seen = {}

    async def complete(self, response_token=None, use_session_token=False):
        seen["verifier"], seen["url"] = self._auth_code_verifier, response_token
        self._session_token = TOKEN
    monkeypatch.setattr(pynintendoparental.Authenticator, "async_complete_login", complete)
    link = "npf54789befb391a838://auth#session_state=s&session_token_code=abc&state=t"
    r = client.post("/settings/nintendo/link", data={"csrf_token": tok, "url": link}, follow_redirects=True)
    assert "Verbunden." in r.text and seen == {"verifier": verifier, "url": link}
    assert TOKEN not in r.text
    with SessionLocal() as db:
        row = db.scalar(select(NintendoLink))
        assert row.session_token == TOKEN and row.pending_verifier is None and row.status == nintendo.OK
        raw = db.execute(text("SELECT session_token FROM nintendo_link")).scalar()
        assert TOKEN not in raw                               # verschlüsselt gespeichert
        archive = zipfile.ZipFile(io.BytesIO(export_zip(db)))
    assert "nintendo_link.json" not in archive.namelist()     # Token nie im Export
    assert all(TOKEN not in archive.read(n).decode() for n in archive.namelist())
    assert TEST_FAMILY in nintendo.due_families()


def test_setup_rejected_and_expired_link(client, parent, monkeypatch):
    import pynintendoparental
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    link = "npf54789befb391a838://auth#session_token_code=abc"
    r = client.post("/settings/nintendo/link", data={"csrf_token": tok, "url": link})
    assert r.status_code == 400 and "abgelaufen" in r.text     # ohne gestartete Einrichtung
    client.post("/settings/nintendo/start", data={"csrf_token": tok})

    async def reject(self, response_token=None, use_session_token=False):
        raise HttpException(400, "invalid_request")
    monkeypatch.setattr(pynintendoparental.Authenticator, "async_complete_login", reject)
    r = client.post("/settings/nintendo/link", data={"csrf_token": tok, "url": link})
    assert r.status_code == 400 and "abgelehnt" in r.text
    with SessionLocal() as db:
        db.scalar(select(NintendoLink)).pending_at = utcnow() - timedelta(minutes=nintendo.PENDING_MINUTES + 1)
        db.commit()
    r = client.post("/settings/nintendo/link", data={"csrf_token": tok, "url": link})
    assert r.status_code == 400 and "abgelaufen" in r.text


def test_assign_shows_today_and_stats_unlink_keeps_history(client, parent, monkeypatch):
    cid, tok = _child(client, parent)
    _linked()
    _fake_fetch(monkeypatch, ([_device()], set()))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    page = client.get("/settings").text
    assert "Wohnzimmer" in page and "nicht zugeordnet" in page and "verbunden" in page
    assert "Nintendo Switch" not in client.get(f"/children/{cid}").text
    with SessionLocal() as db:
        pid = db.scalar(select(NintendoPlayer.id).where(NintendoPlayer.player_key == "p1"))
    r = client.post("/settings/nintendo/assign", data={"csrf_token": tok, "player_id": pid, "child_id": cid},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(NintendoUsage.child_id).where(NintendoUsage.player_id == pid)) == cid

    heute = client.get(f"/children/{cid}").text
    assert "Nintendo Switch" in heute and "Wohnzimmer · Max" in heute and "Zelda" in heute
    assert "0:40 h" in heute or "40 min" in heute
    stats = client.get(f"/children/{cid}/stats").text
    assert 'id="nintendo"' in stats
    assert nintendo.daily(SessionLocal(), cid, 3)[-1] == (sch.today_local(), 40)

    client.post("/settings/nintendo/unlink", data={"csrf_token": tok})
    with SessionLocal() as db:
        assert db.scalar(select(NintendoLink)) is None and db.scalar(select(NintendoConsole)) is None
        rec = db.scalar(select(NintendoUsage).where(NintendoUsage.child_id == cid))
        assert rec.minutes == 40 and rec.player_id is None    # Statistik bleibt
    assert 'id="nintendo"' in client.get(f"/children/{cid}/stats").text
    assert "Verbindung einrichten" in client.get("/settings").text


def test_assign_foreign_ids_404(client, parent, monkeypatch):
    _cid, tok = _child(client, parent)
    _linked()
    _fake_fetch(monkeypatch, ([_device()], set()))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    with SessionLocal() as db:
        pid = db.scalar(select(NintendoPlayer.id))
    assert client.post("/settings/nintendo/assign", data={"csrf_token": tok, "player_id": 999999,
                                                          "child_id": ""}).status_code == 404
    assert client.post("/settings/nintendo/assign", data={"csrf_token": tok, "player_id": pid,
                                                          "child_id": "999999"}).status_code == 404


def test_reauth_shown_in_settings(client, parent, monkeypatch):
    login(client, parent)
    _linked()
    _fake_fetch(monkeypatch, InvalidSessionTokenException(400, "invalid_grant"))
    _run(nintendo.poll_family(TEST_FAMILY, None))
    page = client.get("/settings").text
    assert "nicht mehr gültig" in page and "Neu anmelden" in page


def test_feature_off_hides_everything(client, parent, monkeypatch):
    from app.routes import nintendo as routes, pwa
    off = replace(settings, nintendo=False)
    monkeypatch.setattr(routes, "settings", off)
    monkeypatch.setattr(pwa, "settings", off)
    login(client, parent)
    page = client.get("/settings").text
    assert 'id="nintendo"' not in page
    assert client.post("/settings/nintendo/start", data={"csrf_token": csrf_from(page)}).status_code == 404


def test_retention_rule_and_saas_default(monkeypatch):
    assert any(r.name == "NINTENDO" and r.model is NintendoUsage for r in retention.RULES)
    from app import config
    monkeypatch.setenv("KIDCTL_MODE", "saas")
    monkeypatch.setenv("KIDCTL_MASTER_KEY", "A" * 43)
    assert config.load_settings().nintendo is False
    monkeypatch.setenv("KIDCTL_MODE", "single")
    monkeypatch.setenv("KIDCTL_NINTENDO_INTERVAL", "10")
    s = config.load_settings()
    assert s.nintendo is True and s.nintendo_interval == 60
