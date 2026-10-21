"""Integritätsprüfung: Sind die Schutzmaßnahmen auf diesem Rechner intakt?

Geprüft wird nur, was für die aktuelle Policy relevant ist (z. B. DNS-Filter nur für Kinder mit
Website-Filter). Dateien unter /etc kann der Agent nicht reparieren (läuft mit ProtectSystem) –
das meldet er nur; Laufzeit-Zustand (nftables, Resolver, Mounts) stellt er selbst wieder her.
"""

from __future__ import annotations

import configparser
import json
import pwd
import subprocess
from pathlib import Path

from kidctl_common.protocol import IntegrityCheck

from .dnsfilter import BASE_PORT, NFT_TABLE, UNIT
from .kidmounts import noexec_mounts, safe_home

PAM_FILE = Path("/etc/pam.d/system-login")          # Arch: Block per kidctl-pam.sh
PAM_FILE_DEBIAN = Path("/etc/pam.d/common-account")  # Debian/Ubuntu: per pam-auth-update (Profil „kidctl“)
PAM_MARK = "# kidctl begin"
PAM_LINE = "pam_listfile.so onerr=succeed item=user sense=deny file=/run/kidctl/locked-users"
NSSWITCH = Path("/etc/nsswitch.conf")
FIREFOX_POLICY = Path("/etc/firefox/policies/policies.json")
CHROMIUM_POLICY = Path("/etc/chromium/policies/managed/kidctl.json")
SHARED_NOEXEC = ("/tmp", "/var/tmp", "/dev/shm")
GDM_CONF = Path("/etc/gdm/custom.conf")
# Desktop -> (Programm, an dem er erkannt wird; Markierungsdatei des passenden kidctl-Pakets)
DESKTOP_PACKAGES = {
    "plasma": ("/usr/bin/plasmashell", "/usr/share/kidctl/desktop/plasma"),
    "gnome": ("/usr/bin/gnome-shell", "/usr/share/kidctl/desktop/gnome"),
}


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C"})


def check_pam(debian: Path = PAM_FILE_DEBIAN) -> IntegrityCheck:
    label = "Login-Sperre (PAM)"
    path = debian if debian.exists() else PAM_FILE
    try:
        text = path.read_text()
    except OSError as e:
        return IntegrityCheck(id="pam", label=label, ok=False, detail=f"{path} nicht lesbar: {e}")
    if path == debian:   # pam-auth-update schreibt keine Markierung
        ok = any(PAM_LINE in line and not line.lstrip().startswith("#") for line in text.splitlines())
    else:
        ok = any(line.startswith(PAM_MARK) for line in text.splitlines()) and PAM_LINE in text
    return IntegrityCheck(id="pam", label=label, ok=ok,
                          detail="" if ok else f"kidctl-Block in {path} fehlt oder wurde verändert")


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


def check_noexec(kid_users: set[str], mounts=noexec_mounts, libraries: set[str] = frozenset()) -> list[IntegrityCheck]:
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
    for path in sorted(libraries):  # Spieleordner ohne Launcher-Freigabe
        ok = path in active
        out.append(IntegrityCheck(id=f"noexec:{path}", label=f"noexec-Spieleordner {path}", ok=ok,
                                  detail="" if ok else f"{path} nicht noexec eingehängt"))
    return out


def install_hint(package: str) -> str:
    return f"sudo apt install {package}" if Path("/var/lib/dpkg/status").exists() and not Path(
        "/var/lib/pacman/local").exists() else f"sudo pacman -S {package}"


def check_desktop_packages(packages: dict[str, tuple[str, str]] = DESKTOP_PACKAGES) -> list[IntegrityCheck]:
    """Für jeden installierten Desktop das passende Paket (``kidctl-plasma`` bzw. ``kidctl-gnome``)."""
    out = []
    for desktop, (program, marker) in packages.items():
        if not Path(program).exists():
            continue
        ok = Path(marker).exists()
        out.append(IntegrityCheck(id=f"desktop:{desktop}", label=f"Desktop-Paket kidctl-{desktop}", ok=ok,
                                  detail="" if ok else f"fehlt – {install_hint(f'kidctl-{desktop}')}"))
    return out


def check_gdm_autologin(kid_users: set[str], conf: Path = GDM_CONF) -> list[IntegrityCheck]:
    """GDM bleibt schwarz (kein Anmeldebildschirm), wenn die automatische Anmeldung beim Start abgelehnt wird –
    bei einem Kind also in jeder gesperrten Zeit. Automatische Anmeldung für Kinder daher melden."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(conf.read_text(errors="replace"))
    except (OSError, configparser.Error):
        return []
    daemon = cp["daemon"] if cp.has_section("daemon") else {}
    user = daemon.get("AutomaticLogin", "").strip()
    if daemon.get("AutomaticLoginEnable", "").strip().lower() not in ("true", "1") or user not in kid_users:
        return []
    return [IntegrityCheck(id=f"gdm-autologin:{user}", label=f"GDM-Autologin für {user}", ok=False,
                           detail=f"bei Login-Sperre bleibt der Bildschirm schwarz – in {conf} abschalten")]


def check_tray(icons: dict[str, bool], kid_users: set[str]) -> list[IntegrityCheck]:
    """Tray-Symbol der angemeldeten Kinder sichtbar? (``icons``: User -> sichtbar, laut Tray-App)"""
    return [IntegrityCheck(id=f"tray:{user}", label=f"Tray-Symbol von {user}", ok=visible,
                           detail="" if visible else "nicht sichtbar – unter GNOME die Erweiterung „AppIndicator“ einschalten")
            for user, visible in sorted(icons.items()) if user in kid_users]


LIBRARY_LABELS = {"steam": "Steam-Bibliothek", "heroic": "Heroic-Spieleordner"}


def check_libraries(problems: dict[tuple[str, str], str]) -> list[IntegrityCheck]:
    """Spiele-Ordner im Home statt unter /srv/steam bzw. /srv/heroic (siehe KidMounts.ensure_libraries).
    ``problems``: (Bibliothek, User) -> Meldung."""
    return [IntegrityCheck(id=f"{lib}:{user}", label=f"{LIBRARY_LABELS.get(lib, lib)} von {user}", ok=False,
                           detail=msg)
            for (lib, user), msg in sorted(problems.items())]


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
