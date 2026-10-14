"""Ein fehlerhafter oder kompromittierter Server darf über die Policy nichts außerhalb der
Policy bewirken: keine Admins sperren, keine Dateien außerhalb des Caches schreiben, keine
Unbound-Anweisungen einschleusen, keine unverschlüsselte Verbindung."""

import threading
from datetime import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pydantic import ValidationError

from kidctl_common import protocol as p

from kidctl_agent import cli, dnsfilter, loginlock
from kidctl_agent.connection import Connection


def child(cid=1, **web):
    return {"id": cid, "name": "Max", "rules": [], "web": web}


@pytest.mark.parametrize("users", [{"root\nadmin": 1}, {"max\n": 1}, {"a b": 1}, {"../x": 1}, {"": 1}])
def test_policy_rejects_bad_usernames(users):
    with pytest.raises(ValidationError):
        p.Policy(version="v", timezone="Europe/Berlin", users=users, children=[child()])


def test_policy_accepts_normal_usernames():
    pol = p.Policy(version="v", timezone="Europe/Berlin", users={"max": 1, "lena.m": 1, "kid_2": 1, "pc$": 1},
                   children=[child()])
    assert len(pol.users) == 4


@pytest.mark.parametrize("cid", [0, -1, 60001, 70000])
def test_policy_rejects_child_ids_outside_port_range(cid):
    with pytest.raises(ValidationError):
        p.Policy(version="v", timezone="Europe/Berlin", users={"max": cid}, children=[child(cid)])


@pytest.mark.parametrize("list_id", ["../../etc/x", "a/b", "A", "x.txt", "", "-x"])
def test_blocklist_id_must_be_safe_filename(list_id):
    with pytest.raises(ValidationError):
        p.BlocklistRef(id=list_id)


def test_loginlock_never_writes_injected_names_or_admins(monkeypatch):
    monkeypatch.setattr(loginlock, "is_admin", lambda u: u == "admin")
    lock = loginlock.LoginLock()
    lock.update({"max", "root\nadmin", "admin", "a b"})
    assert (loginlock.RUN_DIR / loginlock.LOCKED_FILE).read_text() == "max\n"


def test_unbound_zones_only_valid_domains():
    evil = 'evil.com." always_nxdomain\ninclude: "/etc/shadow'
    web = p.WebFilter(mode="blacklist", allow=["ok.example.org", evil], deny=["bad.example", "x y.com"],
                      lists=[p.BlocklistRef(id="l")], safesearch=False)
    lines = dnsfilter.build_zones(web, {"l": ["ads.example.com", 'q"uote.com', "UPPER.com"]})
    text = "\n".join(lines)
    assert "include" not in text and "shadow" not in text and 'q"uote' not in text
    assert all(line.count('"') == 2 for line in lines)
    for d in ("ok.example.org", "bad.example", "ads.example.com"):
        assert f'local-zone: "{d}."' in text


@pytest.mark.parametrize("name,ok", [("example.com", True), ("a.b-c.de", True), ("_dmarc.x.org", True),
                                     ("com", False), ("-a.com", False), ("a..com", False), ("a.com.", False),
                                     ("a.com\n", False), ("x" * 64 + ".com", False)])
def test_valid_domain(name, ok):
    assert dnsfilter.valid_domain(name) is ok


def test_enroll_requires_https():
    with pytest.raises(SystemExit, match="https://"):
        cli.enroll("http://kids.example", "ABCD2345")


def test_connection_refuses_plain_ws_without_flag():
    dev = {"server_url": "http://kids.example", "device_id": 1, "token": "t"}
    with pytest.raises(ValueError):
        Connection(dev, None)
    assert Connection({**dev, "insecure_http": True}, None).url.startswith("ws://")
    assert Connection({**dev, "server_url": "https://kids.example"}, None).url.startswith("wss://")


def test_blocklist_fetch_does_not_follow_redirects():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/api/"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{srv.server_port}/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"evil.example\n")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        f = dnsfilter.DnsFilter(run=lambda *a, **k: None)
        f.fetch_lists([p.BlocklistRef(id="l", sha256="0" * 64)],
                      {"server_url": f"http://127.0.0.1:{srv.server_port}", "token": "T"})
    finally:
        srv.shutdown()
    assert [h[0] for h in hits] == ["/api/v1/lists/l"]   # Weiterleitung nicht verfolgt
    assert f.load_list("l") == []
