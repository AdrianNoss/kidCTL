"""Website-Filter auf dem Client: Unbound-Instanz je Kind + nftables-Umleitung.

- Je Kind mit Filter ein Resolver ``kidctl-dns@<kind-id>.service`` (Unbound, DynamicUser) auf
  127.0.0.1/::1, Port ``BASE_PORT + kind-id``; Konfiguration unter /run/kidctl/dns.
- nftables (Tabelle ``inet kidctl_dns``): DNS (Port 53) der Kinder-UIDs per ``meta skuid`` auf
  den Resolver des Kindes umleiten, DNS-over-TLS/QUIC (Port 853) sperren.
- Bekannte DNS-over-HTTPS-Anbieter immer gesperrt (zusätzlich Browser-Policies).
- Sperrlisten holt der Agent beim Server (Geräte-Token, SHA-256 geprüft) und cacht sie.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pwd
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from kidctl_common import protocol as p

from . import loginlock, store

log = logging.getLogger("kidctl.dns")

BASE_PORT = 5300
LOG_DIR = "/run/kidctl-dns"   # RuntimeDirectory der Resolver-Unit (schreibbar für DynamicUser)
UNIT = "kidctl-dns@{}.service"
NFT_TABLE = "kidctl_dns"
RESOLV_CONF = "/etc/resolv.conf"

# DNS-over-HTTPS-Anbieter (umgehen sonst den Filter) + Firefox-Kanarienvogel (NXDOMAIN schaltet
# die automatische DoH-Aktivierung ab)
DOH_DOMAINS = (
    "use-application-dns.net", "dns.google", "dns.google.com", "8888.google", "cloudflare-dns.com",
    "one.one.one.one", "1dot1dot1dot1.cloudflare-dns.com", "dns.quad9.net", "dns9.quad9.net",
    "dns10.quad9.net", "dns11.quad9.net", "doh.opendns.com", "doh.familyshield.opendns.com",
    "dns.nextdns.io", "dns.adguard.com", "dns.adguard-dns.com", "doh.cleanbrowsing.org",
    "dns.mullvad.net", "doh.mullvad.net", "doh.dns.sb", "dns.controld.com", "freedns.controld.com",
    "doh.libredns.gr", "dns.digitale-gesellschaft.ch", "dns.switch.ch", "doh.ffmuc.net",
)

# SafeSearch per CNAME (der Resolver löst das Ziel mit auf)
SAFESEARCH = {
    "forcesafesearch.google.com": ("www.google.com", "www.google.de", "www.google.at", "www.google.ch"),
    "strict.bing.com": ("www.bing.com",),
    "safe.duckduckgo.com": ("duckduckgo.com", "www.duckduckgo.com", "start.duckduckgo.com"),
    "restrictmoderate.youtube.com": ("www.youtube.com", "m.youtube.com", "youtubei.googleapis.com",
                                     "youtube.googleapis.com", "www.youtube-nocookie.com"),
}


# Nur gültige Hostnamen gelangen in die Unbound-Konfiguration (keine Anführungszeichen,
# Leerzeichen oder Zeilenumbrüche -> keine eingeschleusten Unbound-Anweisungen).
_DOMAIN = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)+"
                     r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def valid_domain(name: str) -> bool:
    return bool(_DOMAIN.match(name))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Sperrlisten nur direkt vom Server – kein Weiterreichen des Geräte-Tokens an andere Ziele."""
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def covered(name: str, domains: set[str]) -> bool:
    """``name`` ist ``domains`` oder Subdomain davon."""
    parts = name.split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts)))


def build_zones(web: p.WebFilter, lists: dict[str, list[str]]) -> list[str]:
    """Unbound-Zeilen (local-zone/local-data) mit fester Rangfolge, ohne Doppelte."""
    zones: dict[str, str] = {}  # Name -> Typ
    data: list[str] = []
    allow = {d for d in web.allow if valid_domain(d)}
    for d in DOH_DOMAINS:
        zones[d] = "always_nxdomain"
    if web.safesearch:
        for target, names in SAFESEARCH.items():
            for name in names:
                if name in zones or (web.mode == "whitelist" and not covered(name, allow)):
                    continue
                zones[name] = "redirect"
                data.append(f'local-data: "{name}. CNAME {target}."')
    for d in sorted(allow):
        zones.setdefault(d, "transparent")
    if web.mode == "blacklist":
        for d in web.deny:
            if valid_domain(d):
                zones.setdefault(d, "always_nxdomain")
        for ref in web.lists:
            for d in lists.get(ref.id, ()):
                if valid_domain(d):
                    zones.setdefault(d, "always_nxdomain")
    lines = [f'local-zone: "{name}." {typ}' for name, typ in zones.items()]
    return lines + data


def report_sources(web: p.WebFilter, lists: dict[str, list[str]]) -> dict[str, str]:
    """Zonen, deren Treffer ins Sperrprotokoll sollen, mit Quelle. Werbe-/Tracker-Listen
    (``report=False``) nicht – sonst Hunderte Einträge je Seitenaufruf."""
    src: dict[str, str] = {}
    if web.mode == "whitelist":
        src["."] = "nicht freigegeben"
    if web.mode == "blacklist":
        for ref in web.lists:
            if ref.report:
                for d in lists.get(ref.id, ()):
                    src.setdefault(d, f"Sperrliste {ref.name or ref.id}")
        for d in web.deny:
            src[d] = "von Eltern gesperrt"
    for d in DOH_DOMAINS:
        src[d] = "DNS-over-HTTPS-Anbieter"
    return src


def parse_blocked(line: str) -> tuple[str, str] | None:
    """Unbound log-local-actions: ``info: <zone> always_nxdomain <client> <anfrage> <typ> <klasse>``."""
    parts = line.split()
    try:
        i = parts.index("always_nxdomain")
    except ValueError:
        return None
    if i < 1 or len(parts) < i + 3:
        return None
    return parts[i - 1].rstrip(".") or ".", parts[i + 2].rstrip(".")


def upstreams() -> list[str]:
    """Nameserver des Systems (meist systemd-resolved 127.0.0.53)."""
    out = []
    try:
        for line in Path(RESOLV_CONF).read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver":
                out.append(parts[1])
    except OSError:
        pass
    return out or ["127.0.0.53"]


def unbound_conf(port: int, web: p.WebFilter, zones_file: str, log_file: str = "") -> str:
    """``log_file``: lokale Aktionen (Treffer der Zonen) dorthin statt ins Journal – der Agent liest
    daraus gesperrte Anfragen und leert die Datei (erlaubte Anfragen werden nie gespeichert)."""
    fwd = "\n".join(f"  forward-addr: {a}" for a in upstreams())
    logging_ = (f'  use-syslog: no\n  logfile: "{log_file}"\n  log-local-actions: yes\n'
                if log_file else "  use-syslog: yes\n")
    root = '  local-zone: "." always_nxdomain\n' if web.mode == "whitelist" else ""
    return f"""# kidctl – automatisch erzeugt, nicht bearbeiten
server:
  interface: 127.0.0.1@{port}
  interface: ::1@{port}
  port: {port}
  username: ""
  chroot: ""
  directory: ""
  pidfile: ""
  do-daemonize: no
{logging_}  verbosity: 0
  module-config: "iterator"
  do-not-query-localhost: no
  access-control: 127.0.0.0/8 allow
  access-control: ::1/128 allow
  hide-identity: yes
  hide-version: yes
{root}  include: "{zones_file}"
forward-zone:
  name: "."
{fwd}
"""


def nft_ruleset(ports_by_uid: dict[int, int]) -> str:
    lines = [f"destroy table inet {NFT_TABLE}"]
    if ports_by_uid:
        nat = "\n".join(f"    meta skuid {uid} meta l4proto {{ tcp, udp }} th dport 53 redirect to :{port}"
                        for uid, port in sorted(ports_by_uid.items()))
        flt = "\n".join(f"    meta skuid {uid} meta l4proto {{ tcp, udp }} th dport 853 reject"
                        for uid in sorted(ports_by_uid))
        lines.append(f"""table inet {NFT_TABLE} {{
  chain nat_out {{
    type nat hook output priority -100; policy accept;
{nat}
  }}
  chain filter_out {{
    type filter hook output priority 0; policy accept;
{flt}
  }}
}}""")
    return "\n".join(lines) + "\n"


def _run(*cmd: str, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin", "LC_ALL": "C"})


class DnsFilter:
    def __init__(self, run=_run) -> None:
        self.run = run
        self.dns_dir = loginlock.RUN_DIR / "dns"
        self.lists_dir = store.STATE_DIR / "lists"
        self._applied: dict[int, str] = {}   # Kind -> Hash der laufenden Konfiguration
        self.reportable: dict[int, dict[str, str]] = {}  # Kind -> Zone -> Quelle (fürs Protokoll)
        self.log_dir = Path(LOG_DIR)
        self._nft = None

    # ---------- Sperrlisten ----------

    def fetch_lists(self, refs: list[p.BlocklistRef], device: dict | None) -> None:
        """Fehlende/veraltete Listen holen (blockierend). Bei Fehlern bleibt der Cache."""
        self.lists_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for ref in refs:
            path = self.lists_dir / f"{ref.id}.txt"
            if not ref.sha256 or (path.exists() and _sha(path.read_bytes()) == ref.sha256):
                continue
            if device is None:
                continue
            url = f"{device['server_url'].rstrip('/')}{p.LISTS_PATH}/{ref.id}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {device['token']}"})
            try:
                with _opener.open(req, timeout=60) as resp:
                    body = resp.read()
            except (urllib.error.URLError, OSError) as e:
                log.warning("Sperrliste %s nicht abrufbar (%s) – nutze Cache", ref.id, e)
                continue
            if _sha(body) != ref.sha256:
                log.warning("Sperrliste %s: Prüfsumme passt nicht – verworfen", ref.id)
                continue
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            os.replace(tmp, path)
            log.info("Sperrliste %s aktualisiert", ref.id)

    def load_list(self, list_id: str) -> list[str]:
        try:
            return (self.lists_dir / f"{list_id}.txt").read_text().split()
        except OSError:
            return []

    # ---------- Anwenden ----------

    def apply(self, children: dict[int, tuple[p.WebFilter, set[str]]]) -> None:
        """``children``: Kind-ID -> (Filter, Linux-User). Blockierend (im Thread aufrufen)."""
        self.dns_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        active = {cid: v for cid, v in children.items() if v[0].mode != "off" and v[1]}
        for cid, (web, _users) in active.items():
            port = BASE_PORT + cid
            zones_file = str(self.dns_dir / f"{cid}.zones")
            lists = {r.id: self.load_list(r.id) for r in web.lists}
            zones = "\n".join(build_zones(web, lists)) + "\n"
            conf = unbound_conf(port, web, zones_file, f"{LOG_DIR}/{cid}/blocked.log")
            self.reportable[cid] = report_sources(web, lists)
            digest = _sha((conf + zones).encode())
            if self._applied.get(cid) == digest and self._unit_active(cid):
                continue
            _write(self.dns_dir / f"{cid}.zones", zones)
            _write(self.dns_dir / f"{cid}.conf", conf)
            r = self.run("systemctl", "restart", UNIT.format(cid))
            if r.returncode == 0:
                self._applied[cid] = digest
                log.info("DNS-Filter für Kind %s (%s, %d Regeln) auf Port %d", cid, web.mode,
                         zones.count("\n"), port)
            else:
                log.error("DNS-Filter für Kind %s startet nicht: %s", cid, r.stderr.strip())
        for cid in list(self._applied) + self._running_units():
            if cid not in active:
                self.run("systemctl", "stop", UNIT.format(cid))
                self._applied.pop(cid, None)
                for suffix in ("conf", "zones"):
                    (self.dns_dir / f"{cid}.{suffix}").unlink(missing_ok=True)
        ports = {}
        for cid, (_web, users) in active.items():
            for user in users:
                try:
                    ports[pwd.getpwnam(user).pw_uid] = BASE_PORT + cid
                except KeyError:
                    pass
        ruleset = nft_ruleset(ports)
        if ruleset != self._nft:
            r = self.run("nft", "-f", "-", input=ruleset)
            if r.returncode == 0:
                self._nft = ruleset
                log.info("nftables: DNS-Umleitung für %d Benutzer", len(ports))
            else:
                log.error("nftables-Regeln nicht anwendbar: %s", r.stderr.strip())

    def collect_blocked(self) -> list[tuple[int, str, str]]:
        """Gesperrte Anfragen seit dem letzten Aufruf: (Kind, Domain, Quelle). Liest und leert die
        Logdateien der Resolver; erlaubte Anfragen (transparent) werden verworfen, nie gespeichert."""
        out = []
        for cid, sources in self.reportable.items():
            path = self.log_dir / str(cid) / "blocked.log"
            try:
                with open(path, "r+") as f:
                    lines = f.read().splitlines()
                    f.seek(0)
                    f.truncate()
            except OSError:
                continue
            for line in lines:
                hit = parse_blocked(line)
                if hit is None:
                    continue
                zone, qname = hit
                if zone in sources:
                    out.append((cid, qname if zone == "." else zone, sources[zone]))
        return out

    def reset(self) -> None:
        """Gemerkten Zustand vergessen -> nächstes apply() schreibt alles neu (Reparatur)."""
        self._applied.clear()
        self._nft = None

    def _unit_active(self, cid: int) -> bool:
        return self.run("systemctl", "is-active", "--quiet", UNIT.format(cid)).returncode == 0

    def _running_units(self) -> list[int]:
        r = self.run("systemctl", "list-units", "kidctl-dns@*", "--plain", "--no-legend", "--all")
        out = []
        for line in r.stdout.splitlines():
            name = line.split()[0] if line.split() else ""
            num = name.removeprefix("kidctl-dns@").removesuffix(".service")
            if num.isdigit():
                out.append(int(num))
        return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o644)  # Resolver läuft als DynamicUser
        f.write(content)
    os.replace(tmp, path)
