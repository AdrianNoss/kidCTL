"""Integritätsprüfung: Sind die Schutzmaßnahmen auf diesem Rechner intakt?

Geprüft wird nur, was für die aktuelle Policy relevant ist (z. B. DNS-Filter nur für Kinder mit
Website-Filter). Dateien unter /etc kann der Agent nicht reparieren (läuft mit ProtectSystem) –
das meldet er nur; Laufzeit-Zustand (nftables, Resolver, Mounts) stellt er selbst wieder her.
"""

from __future__ import annotations

import json
import pwd
import subprocess
from pathlib import Path

from kidctl_common.protocol import IntegrityCheck

from .dnsfilter import BASE_PORT, NFT_TABLE, UNIT
from .kidmounts import noexec_mounts, safe_home

PAM_FILE = Path("/etc/pam.d/system-login")
PAM_MARK = "# kidctl begin"
PAM_LINE = "pam_listfile.so onerr=succeed item=user sense=deny file=/run/kidctl/locked-users"
NSSWITCH = Path("/etc/nsswitch.conf")
FIREFOX_POLICY = Path("/etc/firefox/policies/policies.json")
CHROMIUM_POLICY = Path("/etc/chromium/policies/managed/kidctl.json")
SHARED_NOEXEC = ("/tmp", "/var/tmp", "/dev/shm")


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin", "LC_ALL": "C"})


def check_pam() -> IntegrityCheck:
    label = "Login-Sperre (PAM)"
    try:
        text = PAM_FILE.read_text()
    except OSError as e:
        return IntegrityCheck(id="pam", label=label, ok=False, detail=f"{PAM_FILE} nicht lesbar: {e}")
    ok = any(line.startswith(PAM_MARK) for line in text.splitlines()) and PAM_LINE in text
    return IntegrityCheck(id="pam", label=label, ok=ok,
                          detail="" if ok else f"kidctl-Block in {PAM_FILE} fehlt oder wurde verändert")


def check_nsswitch() -> IntegrityCheck:
    label = "Namensauflösung über DNS-Filter (nsswitch)"
    try:
        hosts = next((ln for ln in NSSWITCH.read_text().splitlines() if ln.startswith("hosts:")), "")
    except OSError as e:
        return IntegrityCheck(id="nsswitch", label=label, ok=False, detail=str(e))
    ok = bool(hosts) and "resolve" not in hosts.split()
    return IntegrityCheck(id="nsswitch", label=label, ok=ok,
                          detail="" if ok else f"„resolve“ wieder in {NSSWITCH} ({hosts.strip()})")


def check_browser_policies() -> IntegrityCheck:
    label = "Browser-Policies gegen DNS-over-HTTPS"
    problems = []
    try:
        ff = json.loads(FIREFOX_POLICY.read_text())["policies"]["DNSOverHTTPS"]
        if ff.get("Enabled") is not False or ff.get("Locked") is not True:
            problems.append("Firefox: DoH nicht gesperrt")
    except (OSError, ValueError, KeyError, TypeError):
        problems.append(f"Firefox: {FIREFOX_POLICY} fehlt/ungültig")
    try:
        if json.loads(CHROMIUM_POLICY.read_text()).get("DnsOverHttpsMode") != "off":
            problems.append("Chromium: DoH nicht aus")
    except (OSError, ValueError, AttributeError):
        problems.append(f"Chromium: {CHROMIUM_POLICY} fehlt/ungültig")
    return IntegrityCheck(id="browser", label=label, ok=not problems, detail="; ".join(problems))


def check_noexec(kid_users: set[str], mounts=noexec_mounts) -> list[IntegrityCheck]:
    active = mounts()
    missing = [d for d in SHARED_NOEXEC if d not in active]
    out = [IntegrityCheck(id="noexec", label="noexec für /tmp, /var/tmp, /dev/shm", ok=not missing,
                          detail=f"ohne noexec: {', '.join(missing)}" if missing else "")]
    for user in sorted(kid_users):
        home = safe_home(user)
        if home is None:
            continue
        ok = home in active
        out.append(IntegrityCheck(id=f"home:{user}", label=f"noexec-Home von {user}", ok=ok,
                                  detail="" if ok else f"{home} nicht noexec eingehängt"))
    return out


def check_steam(problems: dict[str, str]) -> list[IntegrityCheck]:
    """Steam-Bibliothek im Home statt unter /srv/steam (siehe KidMounts.ensure_steam)."""
    return [IntegrityCheck(id=f"steam:{user}", label=f"Steam-Bibliothek von {user}", ok=False, detail=msg)
            for user, msg in sorted(problems.items())]


def check_dns(web_children: dict[int, set[str]], run=_run) -> list[IntegrityCheck]:
    """``web_children``: Kind-ID -> Linux-User mit aktivem Website-Filter."""
    if not web_children:
        return []
    rules = [ln.strip() for ln in run("nft", "list", "table", "inet", NFT_TABLE).stdout.splitlines()]

    def has_rule(*parts: str) -> bool:
        return any(all(x in ln for x in parts) for ln in rules)
    out = []
    for cid, users in sorted(web_children.items()):
        problems = []
        if run("systemctl", "is-active", "--quiet", UNIT.format(cid)).returncode != 0:
            problems.append("Resolver läuft nicht")
        for user in sorted(users):
            try:
                uid = pwd.getpwnam(user).pw_uid
            except KeyError:
                continue
            if not has_rule(f"meta skuid {uid} ", "dport 53", f"redirect to :{BASE_PORT + cid}"):
                problems.append(f"DNS-Umleitung für {user} fehlt")
            if not has_rule(f"meta skuid {uid} ", "dport 853", "reject"):
                problems.append(f"DoT-Sperre für {user} fehlt")
        out.append(IntegrityCheck(id=f"dns:{cid}", label=f"Website-Filter Kind {cid}", ok=not problems,
                                  detail="; ".join(problems)))
    return out
