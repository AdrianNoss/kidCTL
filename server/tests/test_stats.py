"""Auswertung: Programmzeit-Berichte, Statistikseite, Domain-Einträge im Sperrprotokoll."""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kidctl_common import protocol as p

from app import webfilter
from app.db import SessionLocal
from app.models import AppUsage
from app.schedule import today_local

from test_policy import _policy, _setup, device
from test_programs import APPS, _sync


def test_app_usage_and_stats_page(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local()
    with device(client, tok, "pc", users=("max", "papa"), map_to=cid) as (ws, _):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        ws.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=75 * 60),
                                            p.UsageEntry(username="max", day=today - timedelta(days=1),
                                                         seconds=30 * 60)]).model_dump_json())
        ws.send_text(p.AppUsageReport(entries=[
            p.AppUsageEntry(username="max", day=today, key="exe:/usr/lib/firefox/firefox", seconds=50 * 60),
            p.AppUsageEntry(username="max", day=today, key="flatpak:org.tuxpaint.Tuxpaint", seconds=20 * 60),
            p.AppUsageEntry(username="max", day=today - timedelta(days=2), key="exe:/usr/bin/konsole", seconds=600),
            p.AppUsageEntry(username="papa", day=today, key="exe:/usr/bin/konsole", seconds=999),  # nicht zugeordnet
            p.AppUsageEntry(username="max", day=today - timedelta(days=30), key="x", seconds=5),     # zu alt
        ]).model_dump_json())
        # kleinerer Zählerstand ändert nichts (Maximum)
        ws.send_text(p.AppUsageReport(entries=[p.AppUsageEntry(
            username="max", day=today, key="exe:/usr/lib/firefox/firefox", seconds=10)]).model_dump_json())
        _sync(ws)
    with SessionLocal() as db:
        rows = {(r.username, r.key): r.seconds for r in db.scalars(select(AppUsage))}
    assert rows == {("max", "exe:/usr/lib/firefox/firefox"): 3000, ("max", "flatpak:org.tuxpaint.Tuxpaint"): 1200,
                    ("max", "exe:/usr/bin/konsole"): 600}

    page = client.get(f"/children/{cid}/stats").text
    assert page.count('class="col"') == 14
    assert "1:15 h von 1:00 h – überzogen um 15 min" in page          # Tooltip heute
    assert "Diese Woche" in page and "Letzte Woche" in page
    names = re.findall(r'<div class="hname">([^<]+)</div>', page)
    assert names == ["Firefox", "Tux Paint", "Konsole"]                # 7 Tage, absteigend
    today_only = re.findall(r'<div class="hname">([^<]+)</div>', client.get(f"/children/{cid}/stats?range=1").text)
    assert today_only == ["Firefox", "Tux Paint"]
    assert f'href="/children/{cid}/stats"' in client.get("/").text


def test_domain_events_in_log(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        _policy(ws)
        ws.send_text(p.BlockedReport(events=[p.BlockedEvent(
            username="max", at=datetime.now(timezone.utc), kind="domain", key="domain:tiktok.com",
            name="tiktok.com", detail="von Eltern gesperrt")]).model_dump_json())
        _sync(ws)
    page = client.get(f"/children/{cid}").text
    assert re.search(r"Website</small>\s*tiktok\.com", page) and "von Eltern gesperrt" in page


def test_blocklist_refs_carry_name_and_report_flag(client, parent, monkeypatch):
    import asyncio
    monkeypatch.setattr(webfilter, "refresh_lists", lambda only=None: asyncio.sleep(0, set()))
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        _policy(ws)
        client.post(f"/children/{cid}/web", data={"csrf_token": tok, "web_mode": "blacklist",
                                                 "list": ["hagezi-multi", "hagezi-nsfw"], "allow": "", "deny": ""})
        refs = {r.id: r for r in _policy(ws).children[0].web.lists}
        assert refs["hagezi-multi"].report is False and refs["hagezi-nsfw"].report is True
        assert refs["hagezi-nsfw"].name == "HaGeZi NSFW"
