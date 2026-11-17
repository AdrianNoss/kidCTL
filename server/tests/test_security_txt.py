"""/.well-known/security.txt (RFC 9116): ohne Anmeldung, Expires dynamisch, Betreiberkontakt optional zuerst."""

from datetime import datetime, timezone

from app.routes import wellknown


def _fields(text: str) -> list[tuple[str, str]]:
    return [tuple(line.split(": ", 1)) for line in text.strip().splitlines()]


def test_served_without_login(client, monkeypatch):
    monkeypatch.delenv("SECURITY_CONTACT", raising=False)
    r = client.get("/.well-known/security.txt", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    f = _fields(r.text)
    assert [v for k, v in f if k == "Contact"] == list(wellknown.PROJECT_CONTACTS)
    assert ("Preferred-Languages", "en, de") in f
    assert ("Policy", "https://adriannoss.github.io/kidCTL/docs/security/") in f
    assert dict(f)["Canonical"].endswith("/.well-known/security.txt")
    expires = datetime.strptime(dict(f)["Expires"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert 364 <= (expires - datetime.now(timezone.utc)).days <= 365


def test_operator_contact_first(client, monkeypatch):
    monkeypatch.setenv("SECURITY_CONTACT", "it@example.org")
    contacts = [v for k, v in _fields(client.get("/.well-known/security.txt").text) if k == "Contact"]
    assert contacts == ["mailto:it@example.org", *wellknown.PROJECT_CONTACTS]
    monkeypatch.setenv("SECURITY_CONTACT", "https://example.org/security")
    assert _fields(client.get("/.well-known/security.txt").text)[0] == ("Contact", "https://example.org/security")
    monkeypatch.setenv("SECURITY_CONTACT", "kein kontakt")
    assert _fields(client.get("/.well-known/security.txt").text)[0] == ("Contact", wellknown.PROJECT_CONTACTS[0])


def test_expires_relative_to_now():
    text = wellknown.security_txt(datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc))
    assert "Expires: 2027-11-17T12:00:00Z" in text
