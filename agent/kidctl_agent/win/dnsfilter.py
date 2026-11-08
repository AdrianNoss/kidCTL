"""Website-Filter unter Windows: lokaler Unbound, Profil der aktiven Sitzung, DNS der Adapter.

Unter Windows löst der Systemdienst ``dnscache`` für alle Programme auf – ein Filter je Benutzer auf DNS-Ebene
ist nicht möglich. Deshalb:
- Ein Unbound (``<Installation>\\unbound\\unbound.exe``, Kindprozess des Agenten) auf ``127.0.0.1:53``/``::1:53``
  mit dem Filter des Kindes, dessen Sitzung gerade aktiv ist; ist ein Elternteil/Admin aktiv (oder niemand),
  leitet er ungefiltert weiter. Bei jedem Wechsel: neu starten, DNS-Cache von Windows leeren.
- Weiterleitung an die ursprünglichen DNS-Server der Adapter (Router: lokale Namen wie ``fritz.box``), sonst
  eigene Rekursion; Suchdomänen eines Adapters (Firmen-VPN der Eltern) an dessen DNS-Server.
- Alle Adapter zeigen auf ``127.0.0.1``/``::1`` (auch neu hinzugekommene, z. B. VPN); die ursprünglichen
  Einstellungen merkt sich der Agent (``windns.json``) und stellt sie wieder her, wenn kein Kind mehr einen
  Filter hat bzw. bei der Deinstallation.
- Zonen, SafeSearch, DoH-Sperren, Sperrlisten und die Auswertung gesperrter Anfragen wie unter Linux
  (``dnsfilter``).
- Weil der Filter für den ganzen Rechner gilt, bekommt der Whitelist-Modus zwei feste Listen: Windows-
  Infrastruktur (``SYSTEM_ALLOW``: Update, Defender, SmartScreen, Konnektivitätstest, Zeit, Zertifikate,
  Lizenzen) ist immer erlaubt; bekanntes Hintergrundrauschen (``SYSTEM_QUIET``: Telemetrie, MSN/Widgets, Wetter-
  Kacheln, Edge-Konfiguration) bleibt gesperrt, landet aber nicht im Sperrprotokoll.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import subprocess
from pathlib import Path

from kidctl_common import protocol as p

from .. import store
from ..dnsfilter import build_zones, covered, load_list, parse_blocked, report_sources, valid_domain

log = logging.getLogger("kidctl.dns")

DNS_DIR = store.PROGRAM_DATA / "dns"
STATE_FILE = "windns.json"
TCPIP = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
LOOPBACK = ("127.0.0.1", "::1")
CREATE_NO_WINDOW = 0x08000000
_IP = re.compile(r"^[0-9a-fA-F:.%]+$")

# Whitelist-Modus: Windows-Infrastruktur, immer erlaubt (inkl. Subdomains; keine Websites mit Inhalten)
SYSTEM_ALLOW = (
    # Windows Update, Übermittlungsoptimierung, Zertifikats-Vertrauenslisten (ctldl.windowsupdate.com)
    "windowsupdate.com", "update.microsoft.com", "windowsupdate.microsoft.com", "delivery.mp.microsoft.com",
    "dsp.mp.microsoft.com", "cwsapp.update.microsoft.com",
    # Defender (Signaturen, Cloud-Schutz), SmartScreen
    "definitionupdates.microsoft.com", "wdcp.microsoft.com", "wdcpalt.microsoft.com", "cp.wd.microsoft.com",
    "smartscreen.microsoft.com", "smartscreen-prod.microsoft.com", "checkappexec.microsoft.com",
    "urs.microsoft.com",
    # Konnektivitätstest (sonst „Kein Internet“), Zeit
    "msftconnecttest.com", "msftncsi.com", "time.windows.com",
    # Sperrlisten/OCSP der Zertifizierungsstellen (sonst hängen Signaturprüfungen bis zum Timeout)
    "crl.microsoft.com", "oneocsp.microsoft.com", "ocsp.msocsp.com", "ocsp.digicert.com", "crl3.digicert.com",
    "crl4.digicert.com", "ocsp.sectigo.com", "crl.sectigo.com", "ocsp.usertrust.com", "crl.usertrust.com",
    "ocsp.comodoca.com", "ocsp.globalsign.com", "crl.globalsign.com", "lencr.org", "pki.goog",
    # Lizenzen (Store-Apps, Aktivierung)
    "licensing.mp.microsoft.com", "sls.microsoft.com",
)
# Whitelist-Modus: gesperrt, aber nicht ins Sperrprotokoll (Hintergrundabfragen von Windows und Edge)
SYSTEM_QUIET = (
    "data.microsoft.com",            # Telemetrie (v10.events…, watson.events…, settings-win…)
    "telemetry.microsoft.com", "msn.com", "img-s-msn-com.akamaized.net", "iris.microsoft.com",
    "weather.microsoft.com", "cdn.onenote.net", "edge.microsoft.com", "config.edge.skype.com",
    "edge-consumer-static.azureedge.net", "wns.windows.com", "xboxservices.com",
)


def system_zones(web: p.WebFilter | None, zones: list[str]) -> list[str]:
    """``SYSTEM_ALLOW`` als erlaubte Zonen (nur Whitelist-Modus, ohne vorhandene Namen zu doppeln)."""
    if web is None or web.mode != "whitelist":
        return []
    have = {line.split('"')[1] for line in zones if line.startswith("local-zone:")}
    return [f'local-zone: "{d}." transparent' for d in SYSTEM_ALLOW if f"{d}." not in have]

PS_SET = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object {
  $_.InterfaceAlias -notlike 'Loopback*' -and (($_.ServerAddresses -join ',') -ne '127.0.0.1') } | ForEach-Object {
  Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex -ServerAddresses ('127.0.0.1', '::1') }
"""
PS_RESTORE = r"""
$ErrorActionPreference = 'SilentlyContinue'
$orig = ConvertFrom-Json @'
@ORIGINAL@
'@
foreach ($a in Get-NetAdapter -IncludeHidden) {
  $g = ([string]$a.InterfaceGuid).ToUpper()
  if (-not ($orig.PSObject.Properties.Name -contains $g)) { continue }
  $o = $orig.$g
  if ($o) { Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses ($o -split '[ ,]+') }
  else { Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ResetServerAddresses } }
"""


# ---------- Adapter (Registry lesen, PowerShell schreiben) ----------

def read_interfaces() -> dict[str, dict[str, str]]:
    """GUID -> {NameServer, DhcpNameServer, Domain, DhcpDomain, configured} aller IPv4-Schnittstellen."""
    import winreg

    out = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TCPIP) as root:
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            vals = {}
            try:
                with winreg.OpenKey(root, guid) as k:
                    for name in ("NameServer", "DhcpNameServer", "Domain", "DhcpDomain", "DhcpIPAddress",
                                 "IPAddress", "EnableDHCP"):
                        try:
                            v, _t = winreg.QueryValueEx(k, name)
                            vals[name] = " ".join(v) if isinstance(v, list) else str(v)
                        except OSError:
                            pass
            except OSError:
                continue
            ip = vals.get("DhcpIPAddress", "") if vals.get("EnableDHCP") == "1" else vals.get("IPAddress", "")
            vals["configured"] = "1" if ip and not ip.startswith("0.0.0.0") else ""
            out[guid.upper()] = vals
    return out


def servers(text: str) -> list[str]:
    return [s for s in re.split(r"[ ,;]+", text or "") if s and _IP.match(s) and s not in LOOPBACK]


def effective(vals: dict[str, str]) -> list[str]:
    """DNS-Server einer Schnittstelle, wie Windows sie nutzt (statisch vor DHCP)."""
    return re.split(r"[ ,;]+", vals["NameServer"]) if vals.get("NameServer") else servers(vals.get("DhcpNameServer", ""))


def run_ps(script: str, *args: str, run=subprocess.run) -> bool:
    r = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script,
             *args], capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        log.warning("PowerShell: %s", r.stderr.decode(errors="replace")[:300])
    return r.returncode == 0


def flush_cache() -> None:
    try:
        ctypes.WinDLL("dnsapi").DnsFlushResolverCache()
    except (AttributeError, OSError):
        pass


# ---------- Unbound ----------

def unbound_conf(web: p.WebFilter | None, zones_file: str, log_file: str, upstreams: list[str],
                 domain_forwards: dict[str, list[str]]) -> str:
    """Konfiguration für Unbound auf Port 53. ``web`` ``None`` = ungefiltert (Eltern aktiv)."""
    filtered = web is not None and web.mode != "off"
    lines = ["# KidCTL – automatisch erzeugt, nicht bearbeiten", "server:",
             "  interface: 127.0.0.1", "  interface: ::1", "  port: 53", '  username: ""', '  chroot: ""',
             '  directory: ""', '  pidfile: ""', "  do-daemonize: no", "  verbosity: 0",
             '  module-config: "iterator"', "  do-not-query-localhost: no",
             "  access-control: 127.0.0.0/8 allow", "  access-control: ::1/128 allow",
             "  hide-identity: yes", "  hide-version: yes", "  qname-minimisation: yes",
             f'  logfile: "{log_file}"', f"  log-local-actions: {'yes' if filtered else 'no'}"]
    if filtered:
        if web.mode == "whitelist":
            lines.append('  local-zone: "." always_nxdomain')
        lines.append(f'  include: "{zones_file}"')
    forwards = {d: a for d, a in sorted(domain_forwards.items()) if valid_domain(d) and a}
    lines += [f'  domain-insecure: "{d}"' for d in forwards]   # lokale Namen (Router, VPN) ohne DNSSEC-Prüfung
    for domain, addrs in forwards.items():
        lines += ["forward-zone:", f'  name: "{domain}."'] + [f"  forward-addr: {a}" for a in addrs]
    if upstreams:
        lines += ["forward-zone:", '  name: "."', "  forward-first: yes"] + [f"  forward-addr: {a}" for a in upstreams]
    return "\n".join(lines) + "\n"


class WinDnsFilter:
    def __init__(self, install_dir: str, run=subprocess.run, popen=subprocess.Popen, interfaces=read_interfaces,
                 flush=flush_cache) -> None:
        self.exe = os.path.join(install_dir, "unbound", "unbound.exe") if install_dir else ""
        self.run, self.popen, self.interfaces, self.flush = run, popen, interfaces, flush
        self.dir = DNS_DIR
        self.lists_dir = store.STATE_DIR / "lists"
        data = store.load(STATE_FILE) or {}
        self.original: dict[str, str] = data.get("original", {})   # GUID -> NameServer vor KidCTL ("" = DHCP)
        self.proc = None
        self._digest: str | None = None
        self.reportable: dict[str, str] = {}   # Zone -> Quelle (aktives Profil)
        self.active_child: int | None = None

    @property
    def available(self) -> bool:
        return bool(self.exe) and os.path.isfile(self.exe)

    # ---------- Adapter ----------

    def _save(self) -> None:
        store.save(STATE_FILE, {"original": self.original})

    def upstreams(self) -> tuple[list[str], dict[str, list[str]]]:
        """(Upstream-Server, Suchdomäne -> Server) aus den ursprünglichen Einstellungen der Adapter."""
        ups: list[str] = []
        doms: dict[str, list[str]] = {}
        for guid, vals in self.interfaces().items():
            if not vals.get("configured"):
                continue
            orig = self.original.get(guid)
            addrs = servers(orig) if orig else servers(vals.get("DhcpNameServer", "")) or \
                servers(vals.get("NameServer", ""))
            for a in addrs:
                if a not in ups:
                    ups.append(a)
            domain = (vals.get("Domain") or vals.get("DhcpDomain") or "").strip().lower().rstrip(".")
            if domain and addrs:
                doms.setdefault(domain, [a for a in addrs])
        return ups, doms

    def needs_redirect(self) -> bool:
        return any(vals.get("configured") and effective(vals)[:1] != ["127.0.0.1"]
                   for vals in self.interfaces().values())

    def redirect_adapters(self) -> None:
        """Alle Adapter auf den lokalen Resolver (Original vorher merken)."""
        changed = False
        for guid, vals in self.interfaces().items():
            if guid not in self.original and vals.get("NameServer", "") not in ("127.0.0.1", "127.0.0.1,::1"):
                self.original[guid] = vals.get("NameServer", "")
                changed = True
        if changed:
            self._save()
        if self.needs_redirect():
            run_ps(PS_SET, run=self.run)
            self.flush()
            log.info("DNS der Adapter auf den lokalen Filter umgestellt")

    def restore_adapters(self) -> None:
        if not self.original:
            return
        import json
        # Werte nur GUIDs und IP-Adressen (aus der Registry) – kein „'@“ möglich, das den Here-String beendet
        if run_ps(PS_RESTORE.replace("@ORIGINAL@", json.dumps(self.original)), run=self.run):
            log.info("DNS der Adapter wiederhergestellt")
            self.original = {}
            self._save()
            self.flush()

    # ---------- Resolver ----------

    def apply(self, child_id: int | None, web: p.WebFilter | None) -> None:
        """Profil anwenden: ``web`` des aktiven Kindes, ``None`` = ungefiltert. Blockierend."""
        self.dir.mkdir(parents=True, exist_ok=True)
        lists = {r.id: load_list(self.lists_dir, r.id) for r in (web.lists if web else [])}
        if web and web.mode != "off":
            lines = build_zones(web, lists)
            zones = "\n".join(lines + system_zones(web, lines)) + "\n"
        else:
            zones = ""
        ups, doms = self.upstreams()
        conf = unbound_conf(web if web and web.mode != "off" else None, str(self.dir / "zones.conf"),
                            str(self.dir / "blocked.log"), ups, doms)
        digest = hashlib.sha256((conf + zones).encode()).hexdigest()
        self.reportable = report_sources(web, lists) if web and web.mode != "off" else {}
        if digest == self._digest and self.running():
            return
        (self.dir / "zones.conf").write_text(zones, encoding="utf-8")
        (self.dir / "unbound.conf").write_text(conf, encoding="utf-8")
        self.stop()
        self.start()
        self._digest = digest
        self.active_child = child_id if web and web.mode != "off" else None
        self.flush()
        log.info("DNS-Filter: %s", f"Kind {child_id} ({web.mode}, {zones.count(chr(10))} Regeln)"
                 if self.active_child else "ungefiltert (kein Kind aktiv)")

    def start(self) -> None:
        if not self.available:
            log.error("Unbound fehlt (%s) – Website-Filter nicht verfügbar", self.exe)
            return
        self.kill_stale()
        self.proc = self.popen([self.exe, "-d", "-c", str(self.dir / "unbound.conf")], cwd=str(self.dir),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=CREATE_NO_WINDOW)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self._digest = None

    def kill_stale(self) -> None:
        """Verwaiste Resolver (Agent abgestürzt) beenden – sie hielten Port 53 fest."""
        if self.exe:
            self.run(["taskkill", "/F", "/IM", "unbound.exe"], capture_output=True, timeout=30,
                     creationflags=CREATE_NO_WINDOW)

    def collect_blocked(self) -> list[tuple[int, str, str]]:
        """Gesperrte Anfragen seit dem letzten Aufruf: (Kind, Domain, Quelle)."""
        path = self.dir / "blocked.log"
        if self.active_child is None or not path.exists():
            return []
        try:
            with open(path, "r+", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
                f.seek(0)
                f.truncate()
        except OSError:
            return []
        out = []
        for line in lines:
            hit = parse_blocked(line)
            if hit is None:
                continue
            zone, qname = hit
            if zone == "." and covered(qname, set(SYSTEM_QUIET)):
                continue   # Hintergrundrauschen von Windows
            if zone in self.reportable:
                out.append((self.active_child, qname if zone == "." else zone, self.reportable[zone]))
        return out

    def problems(self) -> list[str]:
        """Für die Integrität: was am Filter nicht stimmt (leer = in Ordnung)."""
        out = []
        if not self.running():
            out.append("Resolver läuft nicht")
        bad = [g for g, v in self.interfaces().items() if v.get("configured") and effective(v)[:1] != ["127.0.0.1"]]
        if bad:
            out.append(f"{len(bad)} Netzwerkadapter nicht auf den Filter umgestellt")
        return out


def clear_dir(path: Path = DNS_DIR) -> None:
    for name in ("unbound.conf", "zones.conf", "blocked.log"):
        try:
            (path / name).unlink()
        except OSError:
            pass
