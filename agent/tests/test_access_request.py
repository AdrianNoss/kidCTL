"""Freigabe anfragen: Sperrliste je Kind, Tray-Ablauf, Benachrichtigungen, Freigaben „nur heute“."""

import asyncio
from datetime import date, datetime, timedelta, timezone

from kidctl_common import protocol as p
from kidctl_common import tray as t

from kidctl_agent.dnsfilter import site_of
from kidctl_agent.programs import Allowed, classify
from kidctl_agent.procs import ProcInfo
from test_tray import ME, chat_msg, connect, make_agent


def test_site_of():
    assert site_of("img.cdn.example.com") == "example.com"
    assert site_of("news.bbc.co.uk") == "bbc.co.uk"
    assert site_of("example.de.") == "example.de" and site_of("localhost") == "localhost"


def test_recent_list_and_tray_request():
    async def scenario():
        a, _ = make_agent(connected=True)
        w = a.programs
        w.remember(ME, "program", "exe:/usr/bin/socat1", "socat1")
        w.record_domain(ME, "img.youtube.com", "nicht freigegeben")
        w.record_domain(ME, "www.youtube.com", "nicht freigegeben")
        w.record_domain(ME, "dns.google", "DNS-over-HTTPS-Anbieter")          # Freigabe hebt das nicht auf
        c = await connect(a)
        await c.send(t.TrayHello())
        await c.recv()
        await c.recv()
        await c.send(t.TrayGetBlocked())
        blocked = await c.recv(skip_state=True)
        assert [(i.key, i.name, i.count, i.pending) for i in blocked.items] == [
            ("domain:youtube.com", "youtube.com", 2, False), ("exe:/usr/bin/socat1", "socat1", 1, False)]

        await c.send(t.TrayAccessRequest(key="domain:youtube.com", reason="Erklärvideo Mathe"))
        queued = await c.recv(skip_state=True)
        assert isinstance(queued, t.TrayQueued) and queued.kind == "access_request" and queued.online
        after = await c.recv(skip_state=True)
        assert [(i.key, i.pending) for i in after.items][0] == ("domain:youtube.com", True)
        sent = [m for m in a.conn.sent if isinstance(m, p.AccessRequest)]
        assert [(m.access_kind, m.key, m.name, m.reason, m.username) for m in sent] == [
            ("domain", "domain:youtube.com", "youtube.com", "Erklärvideo Mathe", ME)]

        await c.send(t.TrayAccessRequest(key="domain:youtube.com"))           # schon angefragt
        assert "Schon angefragt" in (await c.recv(skip_state=True)).message
        await c.send(t.TrayAccessRequest(key="exe:/usr/bin/bash"))            # nie gesperrt -> nicht anfragbar
        assert "nicht (mehr)" in (await c.recv(skip_state=True)).message
        assert len([m for m in a.conn.sent if isinstance(m, p.AccessRequest)]) == 1
        await a.tray.stop()
    asyncio.run(scenario())


def test_pending_from_chat_and_open_dialog():
    async def scenario():
        a, _ = make_agent()
        a.programs.remember(ME, "program", "exe:/usr/bin/krita", "Krita")
        a.chat.upsert(chat_msg(7, from_child=True, kind="access_request", status="pending", access_kind="program",
                               access_key="exe:/usr/bin/krita", access_name="Krita"))
        assert a.tray_blocked(ME).items[0].pending
        c = await connect(a)
        await c.send(t.TrayHello())
        await c.recv()
        await c.recv()                                                         # Verlauf: Verbindung steht
        await a.open_access_dialog(ME, "exe:/usr/bin/krita")
        msg = await c.recv(skip_state=True)
        assert msg.item.key == "exe:/usr/bin/krita" and msg.item.name == "Krita"
        await a.tray.stop()
    asyncio.run(scenario())


def test_decision_notifies_child():
    async def scenario():
        a, desk = make_agent()
        a._sessions = await a.logind.sessions()
        base = dict(from_child=True, kind="access_request", access_kind="program", access_key="exe:/usr/bin/krita",
                    access_name="Krita")
        await a.on_chat_message(chat_msg(1))                                   # erster Abgleich
        await a.on_chat_message(chat_msg(8, status="pending", **base))
        await a.on_chat_message(chat_msg(8, status="approved", access_scope="today", **base))
        await a.on_chat_message(chat_msg(9, status="pending", **base))
        await a.on_chat_message(chat_msg(9, status="rejected", **base))
        assert desk.sent[-2:] == [(ME, "Freigabe erteilt", "„Krita“ ist heute erlaubt."),
                                  (ME, "Freigabe abgelehnt", "„Krita“ bleibt gesperrt.")]
    asyncio.run(scenario())


def test_temp_grant_expires_with_day(monkeypatch):
    async def scenario():
        a, _ = make_agent()
        today = a.now_local().date()
        child = a.policy.children[0].model_copy(update={
            "program_mode": "enforce", "temp_grants": [p.TempGrant(key="exe:/usr/bin/krita", day=today)]})
        a.policy = a.policy.model_copy(update={"children": [child]})
        calls = []

        async def fake_sync():
            calls.append("sync")
        monkeypatch.setattr(a, "sync_mounts", fake_sync)
        monkeypatch.setattr(a, "sync_dns", fake_sync)
        assert "/usr/bin/krita" in a.policy_exes()
        allowed = next(iter(a.programs.targets().values()))[2] if a.programs.targets() else None
        if allowed is not None:                                                # Admin-Testuser: keine Ziele
            assert allowed.path_ok("/usr/bin/krita")
        await a._day_changed(today)
        assert calls == []
        a.programs.handled.add(("x",))
        await a._day_changed(today + timedelta(days=1))                       # Mitternacht
        assert calls == ["sync", "sync"] and a.programs.handled == set()
        monkeypatch.setattr(a, "now_local", lambda: datetime.combine(today + timedelta(days=1), datetime.min.time()))
        assert "/usr/bin/krita" not in a.policy_exes()
    asyncio.run(scenario())


def test_non_grantable_verdicts():
    a = Allowed(["/usr/bin/kwin_wayland"], set(), steam_root="/srv/steam/kind/")
    info = ProcInfo(pid=5, uid=1001, ppid=1, start=1, exe="/srv/steam/kind/x/y", argv=("/srv/steam/kind/x/y",),
                    tty=False, flatpak_id=None)
    assert classify(info, Allowed(["/x"], {"exe:/usr/bin/steam"}, steam_root="/srv/steam/kind/"),
                    ancestors=lambda: []).grantable is False
    konsole = ProcInfo(pid=6, uid=1001, ppid=1, start=1, exe="/usr/bin/konsole", argv=("/usr/bin/konsole",),
                       tty=False, flatpak_id=None)
    assert classify(konsole, a).grantable is True
