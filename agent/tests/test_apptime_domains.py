"""Programmzeit (geöffnet während gezählter Zeit) und Protokoll gesperrter Domains."""

import getpass
from datetime import date

from kidctl_common import protocol as p

from kidctl_agent import agent as agent_mod
from kidctl_agent import dnsfilter
from kidctl_agent.apptime import AppIndex, AppTime
from kidctl_agent.dnsfilter import DnsFilter, parse_blocked, report_sources
from test_programs import FakeAgent, make_watcher, proc

DAY = date(2026, 10, 14)
KEYS = {"exe:/usr/lib/firefox/firefox", "exe:/usr/bin/kate", "flatpak:org.tuxpaint.Tuxpaint",
        "exe:/usr/lib/steam/steam", "exe:/usr/bin/hp-toolbox"}


def test_app_index_mapping():
    ix = AppIndex(KEYS)
    assert ix.key_for(proc("/usr/lib/firefox/firefox")) == "exe:/usr/lib/firefox/firefox"
    assert ix.key_for(proc("/usr/lib/firefox/crashhelper")) == "exe:/usr/lib/firefox/firefox"  # App-Verzeichnis
    assert ix.key_for(proc("/usr/bin/kate")) == "exe:/usr/bin/kate"
    assert ix.key_for(proc("/usr/bin/konsole")) is None                       # nicht im Katalog
    assert ix.key_for(proc("/app/bin/tuxpaint", flatpak="org.tuxpaint.Tuxpaint")) == "flatpak:org.tuxpaint.Tuxpaint"
    assert ix.key_for(proc("/usr/bin/python3.14", ["python3", "/usr/bin/hp-toolbox"])) == "exe:/usr/bin/hp-toolbox"
    game = proc("/srv/steam/kind/steamapps/common/Spiel/spiel")
    assert ix.key_for(game, "/srv/steam/kind/") == "exe:/usr/lib/steam/steam"
    assert ix.key_for(proc("/usr/bin/kwin_wayland")) is None                  # Systemprozesse zählen nicht


def test_apptime_add_report_prune_persist():
    a = AppTime()
    a.add("kind", DAY, {"exe:/usr/bin/kate", "exe:/usr/lib/firefox/firefox"}, 5.0)
    a.add("kind", DAY, {"exe:/usr/bin/kate"}, 5.0)
    a.add("kind", DAY, {"x"}, 0)                       # keine Zeit -> nichts
    assert a.report_entries(DAY) == [("kind", DAY, "exe:/usr/bin/kate", 10),
                                     ("kind", DAY, "exe:/usr/lib/firefox/firefox", 5)]
    a.local[("kind", "2026-08-01", "alt")] = 9.0
    a.prune(DAY)
    a.save()
    assert AppTime.load().local == a.local and ("kind", "2026-08-01", "alt") not in a.local


def test_agent_counts_open_apps_only_for_counting_users(monkeypatch):
    me = getpass.getuser()
    running = [proc("/usr/lib/firefox/firefox", pid=1), proc("/usr/lib/firefox/firefox", pid=2),
               proc("/usr/bin/kwin_wayland", pid=3)]
    monkeypatch.setattr(agent_mod, "pids_of", lambda uids: [1, 2, 3])
    monkeypatch.setattr(agent_mod, "read_proc", lambda pid: running[pid - 1])
    a = agent_mod.Agent(logind=None, desktop=None)
    a.app_index = AppIndex(KEYS)
    a._count_apps({me}, DAY, 5.0)
    assert a.apptime.local == {(me, DAY.isoformat(), "exe:/usr/lib/firefox/firefox"): 5.0}  # einmal, nicht je Prozess


def test_parse_blocked_lines():
    assert parse_blocked("[1] unbound[1:0] info: tiktok.com. always_nxdomain 127.0.0.1@42699 www.tiktok.com. A IN") \
        == ("tiktok.com", "www.tiktok.com")
    assert parse_blocked("[1] unbound[1:0] info: . always_nxdomain 127.0.0.1@1 example.com. AAAA IN") == (".", "example.com")
    assert parse_blocked("[1] unbound[1:0] info: wikipedia.org. transparent 127.0.0.1@1 de.wikipedia.org. A IN") is None
    assert parse_blocked("[1] unbound[1:0] info: start of service") is None


def test_report_sources_skip_ad_lists():
    web = p.WebFilter(mode="blacklist", deny=["tiktok.com"], lists=[
        p.BlocklistRef(id="ads", name="HaGeZi Multi", report=False), p.BlocklistRef(id="nsfw", name="HaGeZi NSFW")])
    src = report_sources(web, {"ads": ["doubleclick.net"], "nsfw": ["porn.example"]})
    assert "doubleclick.net" not in src
    assert src["porn.example"] == "Sperrliste HaGeZi NSFW" and src["tiktok.com"] == "von Eltern gesperrt"
    assert src["dns.google"] == "DNS-over-HTTPS-Anbieter"
    assert report_sources(p.WebFilter(mode="whitelist"), {})["."] == "nicht freigegeben"


def test_collect_blocked_reads_and_truncates(tmp_path):
    f = DnsFilter(run=lambda *a, **k: None)
    f.log_dir = tmp_path
    f.reportable = {1: {"tiktok.com": "von Eltern gesperrt", ".": "nicht freigegeben"}}
    (tmp_path / "1").mkdir()
    log = tmp_path / "1" / "blocked.log"
    log.write_text("[1] unbound[1:0] info: tiktok.com. always_nxdomain 127.0.0.1@1 www.tiktok.com. A IN\n"
                   "[1] unbound[1:0] info: wikipedia.org. transparent 127.0.0.1@1 de.wikipedia.org. A IN\n"
                   "[1] unbound[1:0] info: ads.x. always_nxdomain 127.0.0.1@1 ads.x. A IN\n"
                   "[1] unbound[1:0] info: . always_nxdomain 127.0.0.1@1 spiel.example. A IN\n")
    assert f.collect_blocked() == [(1, "tiktok.com", "von Eltern gesperrt"), (1, "spiel.example", "nicht freigegeben")]
    assert log.read_text() == ""                       # geleert – erlaubte Anfragen nicht gespeichert
    assert f.collect_blocked() == []


def test_domain_events_deduplicated():
    a = FakeAgent("off")
    w, _, clock = make_watcher(a, {})
    assert w.record_domain("kind", "tiktok.com", "von Eltern gesperrt")
    assert not w.record_domain("kind", "tiktok.com", "von Eltern gesperrt")   # innerhalb 10 min
    clock[0] += 601
    assert w.record_domain("kind", "tiktok.com", "von Eltern gesperrt")
    e = w.pending[0]
    assert e.kind == "domain" and e.key == "domain:tiktok.com" and e.detail == "von Eltern gesperrt"


def test_unbound_conf_logs_to_runtime_file():
    conf = dnsfilter.unbound_conf(5301, p.WebFilter(mode="blacklist"), "/z", "/run/kidctl-dns/1/blocked.log")
    assert 'logfile: "/run/kidctl-dns/1/blocked.log"' in conf and "log-local-actions: yes" in conf
    assert "use-syslog: no" in conf
