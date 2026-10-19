"""Protokollseite: Filter (Art, Zeitraum, Status, Benutzer, Suche), Sortierung, Tage, Häufigste."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import blocklog
from app.config import settings
from app.db import SessionLocal
from app.models import BlockedEvent
from test_policy import _setup

TZ = ZoneInfo(settings.timezone)


def _utc(local: datetime) -> datetime:
    return local.replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


def _add(cid, **kw):
    with SessionLocal() as db:
        db.add(BlockedEvent(child_id=cid, username=kw.pop("user", "mika"), **kw))
        db.commit()


def _seed(cid):
    now = datetime.now(TZ).replace(tzinfo=None)
    today0 = now.replace(hour=0, minute=5, second=0, microsecond=0)
    _add(cid, at=_utc(today0), kind="program", key="exe:/usr/bin/steam", name="Steam", detail="nicht freigegeben")
    _add(cid, at=_utc(today0 + timedelta(minutes=1)), kind="program", key="exe:/usr/bin/steam", name="Steam")
    _add(cid, at=_utc(today0 + timedelta(minutes=2)), kind="domain", key="domain:tiktok.com", name="tiktok.com",
         audit=True)
    _add(cid, at=_utc(today0 - timedelta(minutes=10)), kind="program", key="exe:/usr/bin/konsole",  # gestern 23:55
         name="Konsole", user="mika2")
    _add(cid, at=_utc(today0 - timedelta(days=20)), kind="domain", key="domain:100%_sicher.example",
         name="100%_sicher.example")
    _add(None, at=_utc(today0), kind="program", key="exe:/fremd", name="Fremdes Kind")  # kein/anderes Kind


def _page(client, cid, query=""):
    r = client.get(f"/children/{cid}/protokoll{query}")
    assert r.status_code == 200
    return r.text


def test_filters(client, parent):
    cid, _tok = _setup(client, parent)
    _seed(cid)
    page = _page(client, cid)                                   # Standard: 7 Tage
    assert "4 Einträge" in page and "Fremdes Kind" not in page and "100%_sicher" not in page
    assert "Heute," in page and "Gestern," in page               # Tagesgruppen in Ortszeit
    assert "Häufigste Einträge" in page

    assert "1 Eintrag ·" in _page(client, cid, "?kind=domain")
    page = _page(client, cid, "?kind=program")
    assert "3 Einträge" in page and "tiktok" not in page.split("log-summary")[1]
    assert "2 Einträge" in _page(client, cid, "?range=1&kind=program")          # nur heute
    assert "5 Einträge" in _page(client, cid, "?range=all")
    assert "1 Eintrag" in _page(client, cid, "?status=audit")
    assert "1 Eintrag" in _page(client, cid, "?user=mika2")
    assert "2 Einträge" in _page(client, cid, "?q=STEAM")                        # Groß/klein egal
    assert "1 Eintrag" in _page(client, cid, "?q=freigegeben")                  # auch im Detail
    # % und _ sind in der Suche normale Zeichen
    assert "1 Eintrag" in _page(client, cid, "?range=all&q=100%25_")
    assert "Keine Einträge für diese Filter" in _page(client, cid, "?q=%25%25x")
    # Unbekannte Werte fallen auf den Standard zurück
    assert "4 Einträge" in _page(client, cid, "?kind=boese&range=999&order=x&limit=abc")


def test_order_and_paging(client, parent, monkeypatch):
    cid, _tok = _setup(client, parent)
    _seed(cid)
    page = _page(client, cid, "?range=1&order=old").split('class="log-day"')[1]   # ohne „Häufigste“
    assert page.index("Steam") < page.index("tiktok.com")
    page = _page(client, cid, "?range=1").split('class="log-day"')[1]
    assert page.index("tiktok.com") < page.index("Steam")
    monkeypatch.setattr(blocklog, "PAGE", 2)
    page = _page(client, cid, "?limit=2")
    assert "laden (2 weitere)" in page and "limit=4" in page


def test_filter_url_omits_defaults():
    f = blocklog.LogFilter()
    assert f.url("/p") == "/p"
    assert f.url("/p", kind="domain") == "/p?kind=domain"
    assert blocklog.LogFilter(q="a b").url("/p", range="all") == "/p?range=all&q=a+b"


def test_child_page_links_and_limit(client, parent):
    cid, _tok = _setup(client, parent)
    for i in range(15):
        _add(cid, at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=i), kind="program",
             key=f"exe:/usr/bin/p{i}", name=f"Prog{i:02d}")
    page = client.get(f"/children/{cid}").text
    assert "Ganzes Protokoll" in page and "Die letzten 10 Einträge" in page
    assert f'href="/children/{cid}/protokoll" class="btn secondary">Protokoll</a>' in page   # Kopfzeile
    assert "Prog00" in page and "Prog14" not in page


def test_prune_after_90_days(client, parent):
    cid, _tok = _setup(client, parent)
    now = datetime(2026, 10, 19, 12, 0)
    _add(cid, at=now - timedelta(days=91), kind="program", key="exe:/alt", name="Alt")
    _add(cid, at=now - timedelta(days=89), kind="program", key="exe:/jung", name="Jung")
    _add(None, at=now - timedelta(days=200), kind="domain", key="domain:x.example", name="x.example")
    with SessionLocal() as db:
        assert blocklog.prune(db, now=now) == 2
        assert [e.name for e in db.query(BlockedEvent)] == ["Jung"]
        assert blocklog.prune(db, now=now) == 0
