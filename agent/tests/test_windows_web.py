"""Website-Filter unter Windows (Unbound-Konfiguration, Adapter, Profilwechsel, WFP-Regeln) mit Attrappen."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kidctl_common import protocol as p

from kidctl_agent import store
from kidctl_agent.win import api
from kidctl_agent.win import dnsfilter as wd


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(wd, "DNS_DIR", tmp_path / "dns")


def test_unbound_conf():
    web = p.WebFilter(mode="whitelist", allow=["wikipedia.org"])
    conf = wd.unbound_conf(web, "Z", "L", ["192.168.178.1"], {"fritz.box": ["192.168.178.1"], "bad name": ["1.1.1.1"]})
    assert 'local-zone: "." always_nxdomain' in conf and 'include: "Z"' in conf and "log-local-actions: yes" in conf
    assert 'name: "fritz.box."' in conf and "bad name" not in conf
    assert "forward-first: yes" in conf and "forward-addr: 192.168.178.1" in conf
    free = wd.unbound_conf(None, "Z", "L", [], {})
    assert "include" not in free and "forward-zone" not in free and "log-local-actions: no" in free  # Rekursion


class FakeIf:
    def __init__(self):
        self.data = {"{A}": {"NameServer": "", "DhcpNameServer": "192.168.178.1", "DhcpDomain": "fritz.box",
                             "configured": "1"},
                     "{B}": {"NameServer": "9.9.9.9", "configured": "1"},
                     "{C}": {"NameServer": "", "DhcpNameServer": "", "configured": ""}}

    def __call__(self):
        return self.data


class FakeProc:
    def __init__(self, args, **kw):
        self.args, self.alive = args, True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


def test_adapters_redirect_and_restore(state):
    calls = []
    ifs = FakeIf()

    def run(args, **kw):
        calls.append(args)
        if "Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex" in " ".join(args):
            for v in ifs.data.values():
                v["NameServer"] = "127.0.0.1,::1"
        return SimpleNamespace(returncode=0, stderr=b"")
    f = wd.WinDnsFilter(r"C:\KidCTL", run=run, popen=FakeProc, interfaces=ifs, flush=lambda: None)
    assert f.needs_redirect()
    ups, doms = f.upstreams()
    assert ups == ["192.168.178.1", "9.9.9.9"] and doms == {"fritz.box": ["192.168.178.1"]}
    f.redirect_adapters()
    assert store.load("windns.json") == {"original": {"{A}": "", "{B}": "9.9.9.9", "{C}": ""}}
    assert not f.needs_redirect()
    assert f.upstreams()[0] == ["192.168.178.1", "9.9.9.9"]   # aus den gemerkten Originalen
    calls.clear()
    f2 = wd.WinDnsFilter(r"C:\KidCTL", run=run, popen=FakeProc, interfaces=ifs, flush=lambda: None)   # Neustart
    f2.redirect_adapters()
    assert calls == [] and store.load("windns.json")["original"]["{B}"] == "9.9.9.9"   # Originale nicht überschrieben
    f2.restore_adapters()
    assert '"{B}": "9.9.9.9"' in calls[0][-1] and "@ORIGINAL@" not in calls[0][-1]
    assert store.load("windns.json") == {"original": {}}


def test_apply_profiles_and_blocked_log(state, monkeypatch):
    monkeypatch.setattr(wd.os.path, "isfile", lambda path: True)
    procs = []

    def popen(args, **kw):
        procs.append(FakeProc(args))
        return procs[-1]
    f = wd.WinDnsFilter(r"C:\KidCTL", run=lambda *a, **k: SimpleNamespace(returncode=0), popen=popen,
                        interfaces=FakeIf(), flush=lambda: None)
    web = p.WebFilter(mode="blacklist", deny=["tiktok.com"])
    f.apply(1, web)
    assert len(procs) == 1 and f.active_child == 1
    assert 'local-zone: "tiktok.com." always_nxdomain' in (wd.DNS_DIR / "zones.conf").read_text()
    f.apply(1, web)
    assert len(procs) == 1                                    # unverändert: kein Neustart
    (wd.DNS_DIR / "blocked.log").write_text(
        "[1] unbound[1:0] info: tiktok.com. always_nxdomain 127.0.0.1@5 www.tiktok.com. A IN\n")
    assert f.collect_blocked() == [(1, "tiktok.com", "von Eltern gesperrt")]
    assert (wd.DNS_DIR / "blocked.log").read_text() == ""      # geleert
    f.apply(None, None)                                       # Eltern aktiv
    assert len(procs) == 2 and not procs[0].alive and f.active_child is None
    assert "include" not in (wd.DNS_DIR / "unbound.conf").read_text()


# ---------- WinAgent ----------

class FakeWfp:
    def __init__(self):
        self.rules = None

    def set_rules(self, rules):
        self.rules = set(rules)

    def close(self):
        pass


class FakeDns:
    def __init__(self):
        self.applied, self.redirected, self.restored, self.stopped = [], 0, 0, 0
        self.lists_dir = None

    def apply(self, cid, web):
        self.applied.append((cid, web.mode if web else None))

    def redirect_adapters(self):
        self.redirected += 1

    def restore_adapters(self):
        self.restored += 1

    def stop(self):
        self.stopped += 1

    def collect_blocked(self):
        return [(1, "tiktok.com", "von Eltern gesperrt")]

    def problems(self):
        return []


def test_winagent_web_profiles(state, monkeypatch, tmp_path):
    from test_windows import FakeApi, _policy
    from kidctl_agent.win import agent as wa
    from kidctl_agent.win.accounts import Accounts
    from kidctl_agent.win.desktop import WinDesktop
    from kidctl_agent.win.sessions import WinSessions

    fake = FakeApi()
    monkeypatch.setattr(wa.Updater, "__init__", lambda self, device: setattr(self, "key", None) or None)
    monkeypatch.setattr("kidctl_agent.dnsfilter.fetch_lists", lambda *a: None)
    acc = Accounts(fake)
    agent = wa.WinAgent(WinSessions(acc, fake), WinDesktop(fake), None, acc)
    agent.tray = SimpleNamespace(send_to_user=lambda *a: asyncio.sleep(0, 0), push_states=lambda now: asyncio.sleep(0),
                                 tray_icons=lambda: {}, clients=[])
    agent.dns, agent.wfp = FakeDns(), FakeWfp()
    agent.langs.read = lambda sid: "de-DE"
    kid = "S-1-5-21-1-2-3-1002"

    async def main():
        pol = _policy()
        pol.children[0].web = p.WebFilter(mode="blacklist", deny=["tiktok.com"])
        agent.policy = pol
        fake.sessions = [(2, api.WTSActive, "lisa", 1, 555)]          # Kind vorne
        await agent.tick_once()
        assert agent.dns.applied[-1] == (1, "blacklist") and agent.dns.redirected
        assert agent.wfp.rules == {(kid, 53), (kid, 853)}
        assert {e.key for e in agent.programs.pending} == {"domain:tiktok.com"}
        assert agent.programs.recent_items("lisa")[0].key == "domain:tiktok.com"
        fake.sessions = [(2, 4, "lisa", 1, 555), (3, api.WTSActive, "papa", 1, 600)]   # Benutzerwechsel
        await agent.tick_once()
        assert agent.dns.applied[-1] == (None, None)                 # Eltern vorne: ungefiltert
        assert (kid, None) in agent.wfp.rules                        # Kind im Hintergrund: kein Netz
        pol2 = _policy()
        agent.policy = pol2                                          # Filter aus
        await agent.tick_once()
        assert agent.wfp.rules == set() and agent.dns.restored and agent.dns.stopped
    asyncio.run(main())
