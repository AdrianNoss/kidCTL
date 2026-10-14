"""Integritätsprüfung: Einzelprüfungen und Reparatur/Meldung im Agenten."""

import asyncio
import getpass
import json
import pwd
import subprocess
from datetime import time

from kidctl_common import protocol as p

from kidctl_agent import integrity, store
from kidctl_agent.agent import Agent

ME = getpass.getuser()
UID = pwd.getpwnam(ME).pw_uid

PAM_OK = """account    include    system-auth
# kidctl begin – Login-Sperre Bildschirmzeit (verwaltet von kidctl-pam.sh)
account    [success=2 default=ignore]  pam_listfile.so onerr=succeed item=user sense=deny file=/run/kidctl/locked-users
account    optional                    pam_echo.so file=/run/kidctl/locked-message
account    requisite                   pam_deny.so
# kidctl end
"""


def files(tmp_path, monkeypatch, pam=PAM_OK, hosts="hosts: mymachines files myhostname dns",
          ff={"policies": {"DNSOverHTTPS": {"Enabled": False, "Locked": True}}}, chrome={"DnsOverHttpsMode": "off"}):
    for name, content in (("pam", pam), ("nss", hosts + "\n"), ("ff.json", json.dumps(ff)),
                          ("ch.json", json.dumps(chrome))):
        (tmp_path / name).write_text(content)
    monkeypatch.setattr(integrity, "PAM_FILE", tmp_path / "pam")
    monkeypatch.setattr(integrity, "NSSWITCH", tmp_path / "nss")
    monkeypatch.setattr(integrity, "FIREFOX_POLICY", tmp_path / "ff.json")
    monkeypatch.setattr(integrity, "CHROMIUM_POLICY", tmp_path / "ch.json")


def test_file_checks_ok_and_broken(tmp_path, monkeypatch):
    files(tmp_path, monkeypatch)
    assert integrity.check_pam().ok and integrity.check_nsswitch().ok and integrity.check_browser_policies().ok
    files(tmp_path, monkeypatch, pam="account include system-auth\n",
          hosts="hosts: mymachines resolve [!UNAVAIL=return] files dns",
          ff={"policies": {"DNSOverHTTPS": {"Enabled": True}}}, chrome={"DnsOverHttpsMode": "automatic"})
    pam, nss, br = integrity.check_pam(), integrity.check_nsswitch(), integrity.check_browser_policies()
    assert not pam.ok and "fehlt" in pam.detail
    assert not nss.ok and "resolve" in nss.detail
    assert not br.ok and "Firefox" in br.detail and "Chromium" in br.detail
    (tmp_path / "pam").unlink()
    assert "nicht lesbar" in integrity.check_pam().detail


def test_noexec_checks(monkeypatch):
    import os
    monkeypatch.setattr(integrity, "safe_home", lambda u: "/home/" + u)
    ok = integrity.check_noexec({ME}, mounts=lambda: {"/tmp", "/var/tmp", "/dev/shm", f"/home/{ME}"})
    assert all(c.ok for c in ok) and [c.id for c in ok] == ["noexec", f"home:{ME}"]
    bad = integrity.check_noexec({ME}, mounts=lambda: {"/tmp"})
    assert not bad[0].ok and "/var/tmp" in bad[0].detail and not bad[1].ok


def nft_and_systemctl(rules: str, unit_active=True):
    def run(*cmd, **kw):
        if cmd[0] == "nft":
            return subprocess.CompletedProcess(cmd, 0, rules, "")
        return subprocess.CompletedProcess(cmd, 0 if unit_active else 3, "", "")
    return run


def test_dns_check():
    good = (f"\t\tmeta skuid {UID} meta l4proto {{ tcp, udp }} th dport 53 redirect to :5301\n"
            f"\t\tmeta skuid {UID} meta l4proto {{ tcp, udp }} th dport 853 reject\n")
    assert integrity.check_dns({1: {ME}}, run=nft_and_systemctl(good))[0].ok
    # Teile in verschiedenen Regeln zählen nicht
    split = f"meta skuid {UID} tcp dport 80 accept\nmeta skuid 9 th dport 53 redirect to :5301\n"
    c = integrity.check_dns({1: {ME}}, run=nft_and_systemctl(split))[0]
    assert not c.ok and "DNS-Umleitung" in c.detail and "DoT-Sperre" in c.detail
    c = integrity.check_dns({1: {ME}}, run=nft_and_systemctl(good, unit_active=False))[0]
    assert "Resolver läuft nicht" in c.detail
    assert integrity.check_dns({}) == []


class FakeConn:
    connected = True

    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
        return True


def test_agent_repairs_and_reports_on_change(monkeypatch, tmp_path):
    files(tmp_path, monkeypatch)
    pol = p.Policy(version="v", timezone="Europe/Berlin", users={ME: 1}, children=[p.ChildPolicy(
        id=1, name="Max", rules=[p.DayRule(weekday=d, quota_minutes=60, window_start=time(0),
                                          window_end=time(23, 59)) for d in range(7)],
        web=p.WebFilter(mode="blacklist"))])
    store.save(store.POLICY_FILE, pol.model_dump(mode="json"))
    a = Agent(logind=None, desktop=None, device=None)
    a.conn = FakeConn()
    state = {"broken": True}
    monkeypatch.setattr(integrity, "check_noexec", lambda users: [])

    def fake_dns(children, run=None):
        return [p.IntegrityCheck(id="dns:1", label="Website-Filter Kind 1", ok=not state["broken"],
                                 detail="DNS-Umleitung für x fehlt" if state["broken"] else "")]
    monkeypatch.setattr(integrity, "check_dns", fake_dns)
    repaired = []

    async def fake_sync_dns():
        repaired.append(1)
        state["broken"] = False          # Reparatur wirkt
    monkeypatch.setattr(a, "sync_dns", fake_sync_dns)

    async def nothing():
        pass
    monkeypatch.setattr(a, "sync_mounts", nothing)

    asyncio.run(a.check_integrity())
    assert repaired == [1]
    report = a.conn.sent[-1]
    assert isinstance(report, p.IntegrityReport) and all(c.ok for c in report.checks)
    asyncio.run(a.check_integrity())                   # unverändert -> keine neue Meldung
    assert len(a.conn.sent) == 1
    files(tmp_path, monkeypatch, pam="kaputt\n")       # /etc-Datei: nur melden
    asyncio.run(a.check_integrity())
    assert len(a.conn.sent) == 2 and not next(c for c in a.conn.sent[-1].checks if c.id == "pam").ok
