"""Website-Filter im Agenten: Zonen-Rangfolge, Unbound-Konfiguration, nftables, Listen, Anwenden."""

import hashlib
import io
import subprocess

from kidctl_common import protocol as p

from kidctl_agent import dnsfilter, store
from kidctl_agent.dnsfilter import DnsFilter, build_zones, nft_ruleset, unbound_conf


def zones(web, lists=None):
    out = {}
    for line in build_zones(web, lists or {}):
        if line.startswith("local-zone"):
            name, typ = line.split('"')[1].rstrip("."), line.split()[-1]
            assert name not in out, f"doppelt: {name}"   # Unbound startet sonst nicht
            out[name] = typ
    return out


def test_whitelist_zones_and_safesearch_only_for_allowed():
    web = p.WebFilter(mode="whitelist", allow=["google.com", "wikipedia.org"], safesearch=True)
    z = zones(web)
    assert z["google.com"] == "transparent" and z["wikipedia.org"] == "transparent"
    assert z["www.google.com"] == "redirect"
    assert "www.youtube.com" not in z           # YouTube nicht freigegeben -> keine Hintertür
    assert z["dns.google"] == "always_nxdomain" and z["use-application-dns.net"] == "always_nxdomain"
    assert 'local-data: "www.google.com. CNAME forcesafesearch.google.com."' in build_zones(web, {})
    assert '  local-zone: "." always_nxdomain' in unbound_conf(5301, web, "/x.zones")


def test_blacklist_precedence():
    web = p.WebFilter(mode="blacklist", allow=["ausnahme.de", "dns.google"], deny=["tiktok.com", "ausnahme.de"],
                      lists=[p.BlocklistRef(id="l", sha256="x")], safesearch=True)
    z = zones(web, {"l": ["ads.example.com", "www.bing.com", "tiktok.com", "ausnahme.de"]})
    assert z["dns.google"] == "always_nxdomain"   # DoH gewinnt immer
    assert z["www.bing.com"] == "redirect"        # SafeSearch vor Sperrliste
    assert z["ausnahme.de"] == "transparent"      # Ausnahme vor eigener Sperre/Liste
    assert z["tiktok.com"] == "always_nxdomain" and z["ads.example.com"] == "always_nxdomain"
    assert "local-zone: \".\"" not in unbound_conf(5301, web, "/x.zones")


def test_safesearch_off_and_deny_ignored_in_whitelist():
    web = p.WebFilter(mode="whitelist", allow=["google.com"], deny=["x.de"], safesearch=False)
    z = zones(web)
    assert "www.google.com" not in z and "x.de" not in z


def test_nft_ruleset():
    rs = nft_ruleset({1001: 5301, 1002: 5302})
    assert rs.startswith("destroy table inet kidctl_dns\n")
    assert "meta skuid 1001 meta l4proto { tcp, udp } th dport 53 redirect to :5301" in rs
    assert "meta skuid 1002 meta l4proto { tcp, udp } th dport 853 reject" in rs
    assert nft_ruleset({}) == "destroy table inet kidctl_dns\n"   # keine Kinder: Tabelle weg


def test_upstreams_from_resolv_conf(tmp_path, monkeypatch):
    f = tmp_path / "resolv.conf"
    f.write_text("# x\nnameserver 127.0.0.53\nnameserver 1.1.1.1\noptions edns0\n")
    monkeypatch.setattr(dnsfilter, "RESOLV_CONF", str(f))
    assert dnsfilter.upstreams() == ["127.0.0.53", "1.1.1.1"]
    assert "forward-addr: 1.1.1.1" in unbound_conf(5301, p.WebFilter(mode="blacklist"), "/z")


def test_fetch_lists_verifies_sha_and_keeps_cache(monkeypatch):
    body = b"ads.example.com\n"
    sha = hashlib.sha256(body).hexdigest()
    served = {"body": body}
    seen = []

    def fake_urlopen(req, timeout):
        seen.append((req.full_url, req.headers.get("Authorization")))
        return io.BytesIO(served["body"])
    monkeypatch.setattr(dnsfilter.urllib.request, "urlopen", fake_urlopen)
    f = DnsFilter(run=lambda *a, **k: None)
    dev = {"server_url": "https://kids.example/", "token": "T"}
    f.fetch_lists([p.BlocklistRef(id="l", sha256=sha)], dev)
    assert f.load_list("l") == ["ads.example.com"]
    assert seen == [("https://kids.example/api/v1/lists/l", "Bearer T")]
    f.fetch_lists([p.BlocklistRef(id="l", sha256=sha)], dev)
    assert len(seen) == 1                                     # unverändert -> kein Abruf
    served["body"] = b"manipuliert\n"
    f.fetch_lists([p.BlocklistRef(id="l", sha256="0" * 64)], dev)
    assert f.load_list("l") == ["ads.example.com"]            # falsche Prüfsumme -> Cache bleibt
    assert oct((store.STATE_DIR / "lists").stat().st_mode & 0o777) == "0o700"


def test_apply_restarts_only_on_change_and_cleans_up(monkeypatch):
    import getpass
    import pwd
    me = getpass.getuser()
    calls = []

    def run(*cmd, input=None):
        calls.append((cmd, input))
        rc = 0
        out = "kidctl-dns@7.service loaded active running x\n" if cmd[1:2] == ("list-units",) else ""
        return subprocess.CompletedProcess(cmd, rc, out, "")
    f = DnsFilter(run=run)
    web = p.WebFilter(mode="whitelist", allow=["wikipedia.org"])
    f.apply({3: (web, {me})})
    restarts = [c for c, _ in calls if c[:2] == ("systemctl", "restart")]
    assert restarts == [("systemctl", "restart", "kidctl-dns@3.service")]
    assert (f.dns_dir / "3.conf").read_text().count("port: 5303") == 1
    nft = [i for c, i in calls if c[0] == "nft"]
    assert f"meta skuid {pwd.getpwnam(me).pw_uid}" in nft[0]
    stops = [c for c, _ in calls if c[:2] == ("systemctl", "stop")]
    assert stops == [("systemctl", "stop", "kidctl-dns@7.service")]   # verwaiste Instanz
    calls.clear()
    f.apply({3: (web, {me})})                                          # unverändert
    assert not [c for c, _ in calls if c[:2] == ("systemctl", "restart") or c[0] == "nft"]
    calls.clear()
    f.apply({3: (p.WebFilter(mode="off"), {me})})                      # Filter aus
    assert ("systemctl", "stop", "kidctl-dns@3.service") in [c for c, _ in calls]
    assert [i for c, i in calls if c[0] == "nft"] == ["destroy table inet kidctl_dns\n"]
    assert not (f.dns_dir / "3.conf").exists()
