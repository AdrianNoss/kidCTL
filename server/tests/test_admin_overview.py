"""Verwaltungsbereich 2, Etappe 2: Übersicht – Kacheln, Suche, Filter, Sortierung, Blättern (alles im Server)."""

import re
from datetime import timedelta

import pytest

from app import operators, web
from app.config import Settings
from app.models import Child, Device, Family, utcnow
from app.tenant import family_session
from conftest import platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _ids(html):
    return [int(x) for x in re.findall(r'<td><a href="/admin/families/(\d+)"><strong>', html)]


@pytest.fixture
def data(client, saas):
    """Familie 1 (Testfamilie) plus vier weitere mit unterschiedlichem Stand."""
    now = utcnow()
    with platform_db() as db:
        specs = [("Müller", "standard", "trial", now + timedelta(days=3), None),
                 ("Meier", "standard", "active", None, None),
                 ("Schulze-Müller", "standard", "past_due", None, now - timedelta(days=1)),
                 ("Zett", "free", "active", None, None)]
        ids = []
        for name, plan, status, trial, paused in specs:
            f = Family(name=name, plan=plan, billing_status=status, trial_ends_at=trial, paused_at=paused,
                       paid_until=now - timedelta(days=30) if status == "past_due" else None)
            db.add(f)
            db.flush()
            ids.append(f.id)
        db.commit()
    with family_session(ids[0]) as db:                     # Müller: 2 Kinder, veralteter, offline Agent
        db.add_all([Child(name="A"), Child(name="B"),
                    Device(name="PC", token_hash="m" * 64, agent_version="0.20.0", protocol_unsupported=True,
                           last_seen_at=now - timedelta(days=3))])
        db.commit()
    with family_session(ids[1]) as db:                     # Meier: aktiv, gerade gesehen
        db.add_all([Child(name="C"), Device(name="PC", token_hash="n" * 64, agent_version="0.33.0",
                                            last_seen_at=now)])
        db.commit()
    return ids


def test_tiles_and_search(client, operator, data):  # noqa: F811
    admin_login(client, operator)
    page = client.get("/admin").text
    tiles = dict(re.findall(r'tile-label">([^<]+)</div><div class="tile-value">(\d+)', page))
    assert tiles["Familien"] == "5" and tiles["Kinder"] == "3" and tiles["in Testphase"] == "1"
    assert tiles["Testphase endet ≤ 7 Tage"] == "1" and tiles["im Ruhemodus"] == "1"
    assert _ids(client.get("/admin?q=müller").text) == [data[0], data[2]]        # Teilwort, ohne Groß/klein
    assert _ids(client.get("/admin?q=MÜLL").text) == [data[0], data[2]]
    assert _ids(client.get(f"/admin?q=%23{data[1]}").text) == [data[1]]         # #ID
    assert _ids(client.get(f"/admin?q={data[3]}").text) == [data[3]]
    assert "Keine Familie passt" in client.get("/admin?q=gibtsnicht").text


def test_filters_and_combinations(client, operator, data):  # noqa: F811
    admin_login(client, operator)
    assert _ids(client.get("/admin?status=trial").text) == [data[0]]
    assert _ids(client.get("/admin?status=trial_ending").text) == [data[0]]
    assert _ids(client.get("/admin?status=paused").text) == [data[2]]
    assert _ids(client.get("/admin?status=active").text) == [1, data[1], data[3]]
    assert _ids(client.get("/admin?plan=free").text) == [1, data[3]]
    assert _ids(client.get("/admin?plan=free&status=active&q=zett").text) == [data[3]]
    assert _ids(client.get("/admin?issue=offline").text) == [data[0]]
    assert _ids(client.get("/admin?issue=outdated").text) == [data[0]]
    assert _ids(client.get("/admin?issue=outdated&plan=free").text) == []
    page = client.get("/admin?status=trial&plan=standard").text           # Entfernen-Chips behalten andere Filter
    assert 'href="/admin?plan=standard"' in page and 'href="/admin?status=trial"' in page
    assert '<option value="trial" selected>' in page and '<option value="standard" selected>' in page


def test_advanced_filters(client, operator, data):  # noqa: F811
    admin_login(client, operator)
    with platform_db() as db:                              # Meier registriert vor 40 Tagen, Zett vor 100
        for fid, days in ((data[1], 40), (data[3], 100)):
            db.get(Family, fid).created_at = utcnow() - timedelta(days=days)
        db.commit()
    day = (utcnow() - timedelta(days=50)).date().isoformat()
    assert _ids(client.get(f"/admin?until={day}").text) == [data[3]]
    assert data[3] not in _ids(client.get(f"/admin?since={day}").text)
    assert _ids(client.get("/admin?since=kaputt").text) == [1, *data]    # ungültiges Datum wird ignoriert
    idle = _ids(client.get("/admin?idle=7").text)                         # Meier gerade gesehen, Müller vor 3 Tagen
    assert data[1] not in idle and data[0] not in idle and data[3] in idle


def test_empty_form_fields_redirect_to_clean_url(client, operator, data):  # noqa: F811
    admin_login(client, operator)
    r = client.get("/admin?q=&status=&plan=free&issue=&idle=&since=&until=", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin?plan=free"
    page = client.get("/admin?q=zett&plan=free").text
    assert 'href="/admin?plan=free"' in page and 'href="/admin?q=zett"' in page   # Chips entfernen je einen Filter
    assert "Alle entfernen" in page


@pytest.mark.parametrize("key", list(operators.SORTS))
def test_every_sort(client, operator, data, key):  # noqa: F811
    admin_login(client, operator)
    up, down = _ids(client.get(f"/admin?sort={key}").text), _ids(client.get(f"/admin?sort=-{key}").text)
    assert sorted(up) == sorted(down) == sorted([1, *data])
    with platform_db() as db:
        rows = operators.families(db)
    expected = [r.id for r in operators.select_rows(rows, sort=key)]
    assert up == expected and down == [r.id for r in operators.select_rows(rows, sort=f"-{key}")]
    assert f'href="/admin?sort=-{key}"' in client.get(f"/admin?sort={key}").text   # Klick dreht um


def test_activity_uses_newest_and_agent_oldest(client, operator, data):  # noqa: F811
    with family_session(data[1]) as db:
        db.add(Device(name="Alt", token_hash="o" * 64, agent_version="0.31.0", last_seen_at=utcnow() - timedelta(days=9)))
        db.commit()
    with platform_db() as db:
        r = next(x for x in operators.families(db) if x.id == data[1])
    assert r.oldest == "0.31.0" and (utcnow() - r.activity) < timedelta(minutes=1)
    admin_login(client, operator)
    assert "gerade eben" in client.get(f"/admin?q=%23{data[1]}").text


def test_paging_keeps_parameters(client, operator, saas, monkeypatch):  # noqa: F811
    monkeypatch.setattr(operators, "PAGE_SIZE", 2)
    with platform_db() as db:
        db.add_all([Family(name=f"Familie {i}") for i in range(5)])
        db.commit()
    admin_login(client, operator)
    first = client.get("/admin?q=familie&sort=-id").text
    assert "Seite 1 von 3" in first and 'href="/admin?q=familie&amp;sort=-id&amp;page=2"' in first
    second = client.get("/admin?q=familie&sort=-id&page=2").text
    assert len(_ids(second)) == 2 and _ids(second)[0] < _ids(first)[-1]
    assert "Seite 3 von 3" in client.get("/admin?q=familie&sort=-id&page=99").text   # über das Ende: letzte Seite


def test_relative_time_handles_both_kinds():
    from datetime import datetime, timezone
    from app.web import fmt_relative
    assert fmt_relative(datetime.now(timezone.utc) - timedelta(hours=2)) == "vor 2 Std."
    assert fmt_relative(utcnow() - timedelta(minutes=5)) == "vor 5 Min."
    assert fmt_relative(utcnow() - timedelta(days=3)) == "vor 3 Tagen"
    assert fmt_relative(None) == "–"

