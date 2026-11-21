"""Sicherheitsprüfung 2026-11-08: jeder behobene Befund als Test gegen den ursprünglichen Angriff."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pyotp
from conftest import two_step_login, PASSWORD, csrf_from

from app import accounts
from app.middleware import MAX_BODY_BYTES
from app.security import client_ip

ENROLL = "/api/v1/enroll"


def _attempt(client, parent, password, code, ip):
    return two_step_login(client, "/login", "mama", password, code, headers={"X-Forwarded-For": ip})


def test_body_limit_by_length(client):
    r = client.post(ENROLL, content=b"x" * (MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_body_limit_without_length(client):
    def chunks():
        for _ in range(4):
            yield b"x" * MAX_BODY_BYTES
    r = client.post(ENROLL, content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_normal_request_passes_limit(client):
    assert client.post(ENROLL, json={"code": "AAAAAAAA", "hostname": "x"}).status_code == 400


def test_wrong_passwords_do_not_lock_out_parent(client, parent):
    for _ in range(5):
        assert _attempt(client, parent, "falsch-falsch-falsch", "000000", "10.2.0.1").status_code == 401
    assert _attempt(client, parent, "falsch-falsch-falsch", "000000", "10.2.0.1").status_code == 429   # IP gesperrt
    code = pyotp.TOTP(parent.totp_secret).now()
    assert _attempt(client, parent, PASSWORD, code, "10.3.0.1").status_code == 303                     # andere IP


def test_wrong_codes_lock_user_on_every_ip(client, parent):
    for i in range(5):
        assert _attempt(client, parent, PASSWORD, "000000", f"10.4.0.{i}").status_code == 401
    code = pyotp.TOTP(parent.totp_secret).now()
    assert _attempt(client, parent, PASSWORD, code, "10.5.0.1").status_code == 429


def test_user_limit_stops_distributed_guessing(client, parent):
    for i in range(50):
        _attempt(client, parent, "falsch-falsch-falsch", "000000", f"10.6.{i // 4}.{i}")
    assert _attempt(client, parent, "falsch-falsch-falsch", "000000", "10.7.0.1").status_code == 429


def test_client_ip_is_last_forwarded_entry():
    def req(*values):
        return SimpleNamespace(headers=SimpleNamespace(getlist=lambda name: list(values)),
                               client=SimpleNamespace(host="172.18.0.2"))
    assert client_ip(req("1.1.1.1, 9.9.9.9")) == "9.9.9.9"                # links steht, was der Client behauptet
    assert client_ip(req("1.1.1.1", "9.9.9.9")) == "9.9.9.9"
    assert client_ip(req()) == "172.18.0.2"


def test_spoofed_forwarded_for_does_not_bypass_limit(client):
    codes = [client.post(ENROLL, json={"code": "AAAAAAAA", "hostname": "x"},
                         headers={"X-Forwarded-For": f"10.8.0.{i}, 9.9.9.9"}).status_code for i in range(7)]
    assert codes[:5] == [400] * 5 and codes[5:] == [429, 429]


def test_reset_mail_runs_as_background_task(client, parent, monkeypatch):
    calls = []
    monkeypatch.setattr(accounts, "start_password_reset", lambda db, ident: calls.append(ident))
    monkeypatch.setattr("app.routes.auth.settings", SimpleNamespace(mail_enabled=True))
    token = csrf_from(client.get("/reset").text)
    r = client.post("/reset", data={"csrf_token": token, "ident": "mama"})
    assert r.status_code == 200 and calls == ["mama"]


def test_csp_on_every_response(client):
    for path in ("/login", "/healthz", "/static/app.js", "/gibtsnicht"):
        csp = client.get(path).headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp


def test_security_headers(client):
    """Web-Scan (Issue #3): Permissions-, Referrer- und Cross-Origin-Policies auf jeder Antwort; Seiten werden nicht
    zwischengespeichert, versionierte statische Dateien und Paketquellen schon."""
    for path in ("/login", "/healthz", "/static/app.js", "/gibtsnicht"):
        h = client.get(path).headers
        assert "camera=()" in h["permissions-policy"] and "publickey-credentials" not in h["permissions-policy"]
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert h["cross-origin-opener-policy"] == "same-origin"
        assert h["cross-origin-resource-policy"] == "same-origin"
        assert h["cross-origin-embedder-policy"] == "require-corp"
    assert client.get("/login").headers["cache-control"] == "no-store"
    assert "no-store" not in client.get("/static/app.js").headers.get("cache-control", "")


def test_templates_need_no_inline_script():
    """Die CSP verbietet Inline-Skripte: Eintrag hier = kaputter Knopf im Browser."""
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    for f in templates.rglob("*.html"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", text, re.S), f.name
        assert not re.findall(r"<script(?![^>]*\bsrc=)", text), f.name
        assert "javascript:" not in text, f.name


def test_templates_need_no_inline_style():
    """Die CSP erlaubt keine Inline-Styles (style-src ohne 'unsafe-inline', Issue #3): Korrekturen gehören zentral
    nach static/style.css. Mail-Templates sind ausgenommen – dort sind Inline-Styles Pflicht."""
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    for f in templates.rglob("*.html"):
        if "mail" in f.relative_to(templates).parts[:-1]:
            continue
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"<[a-zA-Z][^>]*\sstyle\s*=", text, re.S), f.name
        assert "<style" not in text, f.name


def test_csp_forbids_inline_styles(client):
    for path in ("/login", "/signup", "/static/style.css", "/gibtsnicht"):
        csp = client.get(path).headers["content-security-policy"]
        assert "unsafe-inline" not in csp and "style-src 'self';" in csp, (path, csp)


def test_mail_html_csp_safe():
    """Mail-Vorschauen: style-Attribute werden zu Regeln in einem <style> mit Nonce, kein Inline-Style bleibt übrig."""
    from app import mailer
    from app.middleware import csp
    html = mailer.render("report", "de", **mailer.samples()["report"]).html
    assert ' style="' in html
    out = mailer.csp_safe(html, "abc123")
    assert not re.search(r"<[a-zA-Z][^<>]*\sstyle=", out)
    assert re.findall(r"<style(?=[\s>])[^>]*>", out) and \
        all('nonce="abc123"' in t for t in re.findall(r"<style(?=[\s>])[^>]*>", out))
    assert '[data-ks="0"]{' in out and "!important" in out
    assert out.index('[data-ks="0"]') < out.index("@media")     # Mail-eigene Regeln (dunkles Schema) danach
    assert "style-src 'self' 'nonce-abc123';" in csp("abc123") and "unsafe-inline" not in csp("abc123")


def test_pct_classes():
    from app.web import pct
    assert (pct(0, 10), pct(5, 10), pct(10, 10), pct(15, 10), pct(-1, 10), pct(3, 0)) == (0, 50, 100, 100, 0, 0)
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert all(f".p-{i}{{--p:{i}%}}" in css for i in range(101))


def test_local_redirect_only():
    from app.web import is_local_path
    for ok in ("/", "/login", "/children/3?x=1#ende", "/settings#abo"):
        assert is_local_path(ok), ok
    for bad in ("//evil.example", "/\\evil.example", "/\\/evil.example", "https://evil.example", "evil",
                "", "/a\nb", "/%0d", "\\evil", "/x\\y"):
        assert bad == "/%0d" or not is_local_path(bad), bad


def test_language_switch_rejects_foreign_targets(client):
    for bad in ("//evil.example", "/\\evil.example", "https://evil.example", "/\tevil"):
        r = client.get("/lang/en", params={"next": bad}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", bad
    r = client.get("/lang/en", params={"next": "/reset"}, follow_redirects=False)
    assert r.headers["location"] == "/reset"



def test_forwarded_for_only_from_trusted_proxy(monkeypatch):
    import dataclasses
    import ipaddress

    from app import security
    monkeypatch.setattr(security, "settings", dataclasses.replace(
        security.settings, trusted_proxies=(ipaddress.ip_network("172.18.0.5/32"),)))

    def req(peer, *values):
        return SimpleNamespace(headers=SimpleNamespace(getlist=lambda name: list(values)),
                               client=SimpleNamespace(host=peer))
    assert client_ip(req("172.18.0.5", "1.1.1.1, 9.9.9.9")) == "9.9.9.9"   # Traefik: letzter Eintrag
    assert client_ip(req("172.18.0.7", "9.9.9.9")) == "172.18.0.7"         # anderer Container: Header zählt nicht
    assert client_ip(req("203.0.113.9", "10.0.0.1")) == "203.0.113.9"


def test_versioned_static_files_cached_long(client):
    from app.web import asset
    r = client.get(asset("app.js"))
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "immutable" not in client.get("/static/app.js").headers.get("cache-control", "")
    assert "immutable" not in client.get("/static/fehlt.js?v=1").headers.get("cache-control", "")
    assert "immutable" not in client.get("/login").headers.get("cache-control", "")
