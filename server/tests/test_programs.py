"""Programm-Whitelist (Server): Katalog, Freigaben -> Policy, Sperrprotokoll."""

from datetime import datetime, timezone

from sqlalchemy import select

from kidctl_common import protocol as p

from app.db import SessionLocal
from app.models import BlockedEvent, DeviceApp

from conftest import recv
from test_policy import _policy, _setup, device

APPS = [p.AppEntry(key="exe:/usr/lib/firefox/firefox", name="Firefox", icon="firefox",
                   desktop_id="firefox.desktop"),
        p.AppEntry(key="flatpak:org.tuxpaint.Tuxpaint", name="Tux Paint", desktop_id="org.tuxpaint.Tuxpaint.desktop"),
        p.AppEntry(key="exe:/usr/bin/konsole", name="Konsole")]


def _sync(ws):
    ws.send_text(p.Ping().model_dump_json())
    while not isinstance(recv(ws, keep_policy=True), p.Pong):
        pass


def test_catalog_selection_and_policy(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        pol = _policy(ws)
        assert pol.children[0].program_mode == "off" and pol.children[0].allowed_programs == []
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        page = client.get(f"/children/{cid}").text
        assert "Firefox" in page and "Tux Paint" in page and "Konsole" in page

        r = client.post(f"/children/{cid}/programs", data={
            "csrf_token": tok, "program_mode": "enforce",
            "app": ["exe:/usr/lib/firefox/firefox", "flatpak:org.tuxpaint.Tuxpaint", "javascript:boese"]},
            follow_redirects=False)
        assert r.status_code == 303
        pol = _policy(ws)
        assert pol.children[0].program_mode == "enforce"
        assert pol.children[0].allowed_programs == ["exe:/usr/lib/firefox/firefox", "flatpak:org.tuxpaint.Tuxpaint"]

        # Abwahl -> Policy ohne Firefox; ungültiger Modus abgelehnt
        client.post(f"/children/{cid}/programs", data={"csrf_token": tok, "program_mode": "audit",
                                                       "app": ["flatpak:org.tuxpaint.Tuxpaint"]})
        pol = _policy(ws)
        assert pol.children[0].program_mode == "audit"
        assert pol.children[0].allowed_programs == ["flatpak:org.tuxpaint.Tuxpaint"]
        assert client.post(f"/children/{cid}/programs",
                           data={"csrf_token": tok, "program_mode": "x"}).status_code == 400

        # Neuer Bericht ersetzt den alten; freigegebene, aber deinstallierte App bleibt sichtbar
        ws.send_text(p.ReportApps(apps=APPS[:1]).model_dump_json())
        _sync(ws)
        with SessionLocal() as db:
            assert [a.key for a in db.scalars(select(DeviceApp))] == [APPS[0].key]
        assert "nicht mehr installiert" in client.get(f"/children/{cid}").text


def test_blocked_log(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", users=("max", "papa"), map_to=cid) as (ws, _):
        _policy(ws)
        now = datetime.now(timezone.utc)
        ws.send_text(p.BlockedReport(events=[
            p.BlockedEvent(username="max", at=now, key="exe:/usr/bin/konsole", name="Konsole"),
            p.BlockedEvent(username="max", at=now, key="exe:/usr/bin/bash", name="bash",
                           detail="interaktive Shell", audit=True),
            p.BlockedEvent(username="papa", at=now, key="exe:/usr/bin/x", name="X"),  # nicht zugeordnet
        ]).model_dump_json())
        _sync(ws)
        with SessionLocal() as db:
            events = db.scalars(select(BlockedEvent)).all()
            assert {e.username for e in events} == {"max"} and len(events) == 2
            assert all(e.at.tzinfo is None for e in events)
        page = client.get(f"/children/{cid}").text
        assert "Konsole" in page and "interaktive Shell" in page and "nur protokolliert" in page
