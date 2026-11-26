"""Fakes für IMAP, GitHub, Modell und SMTP – keine echten Netzzugriffe."""

from __future__ import annotations

import json
import os
import sys
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from feature_inbox.config import Config  # noqa: E402
from feature_inbox.state import State  # noqa: E402

RID = "0b9c5f2e-6c1a-4a3e-9d4b-2f6e8a1c7d01"


def v1_mail(title="Nintendo Switch anbinden", desc="Bitte die Spielzeit der Switch anzeigen.", rid=RID,
            contact="eltern@example.org", category="Neue Plattform/Gerät", importance="hoch") -> bytes:
    msg = EmailMessage()
    msg["From"] = "kidctl@app.kidctl.de"
    msg["To"] = "features@kidctl.de"
    msg["Subject"] = f"[KidCTL Feature] {title}"
    msg.set_content(
        "KIDCTL-FEATURE-REQUEST v1\n"
        f"ID: {rid}\nDatum: 2026-11-26T10:00:00+01:00\nKategorie: {category}\nWichtigkeit: {importance}\n"
        f"KidCTL-Version: 0.48.0\nKontakt: {contact}\n---\nTitel: {title}\n---\n{desc}\n"
    )
    return msg.as_bytes()


class FakeBox:
    def __init__(self, mails):
        self.mails = {str(i).encode(): raw for i, raw in enumerate(mails, 1)}
        self.moved: list[tuple[bytes, str]] = []

    def unseen(self):
        done = {u for u, _ in self.moved}
        return [(u, r) for u, r in self.mails.items() if u not in done]

    def move(self, uid, folder):
        self.moved.append((uid, folder))


class FakeGitHub:
    """Zeichnet jeden Aufruf auf; erlaubt sind nur Lesen der Liste und Anlegen."""

    def __init__(self, issues=None, fail_create=False):
        self.issues = list(issues or [])
        self.calls: list[str] = []
        self.created: list[dict] = []
        self.fail_create = fail_create

    def feature_issues(self, state="all"):
        self.calls.append(f"list:{state}")
        return [dict(i) for i in self.issues]

    def create_issue(self, title, body):
        self.calls.append("create")
        if self.fail_create:
            from feature_inbox.github import GitHubError
            raise GitHubError("boom")
        number = 100 + len(self.created)
        from feature_inbox.github import LABELS
        rec = {"number": number, "title": title, "body": body, "labels": list(LABELS), "state": "open"}
        self.created.append(rec)
        self.issues.append(rec)
        return number


class FakeAnthropicClient:
    """Imitiert ``client.messages.create`` und gibt eine vorgegebene JSON-Antwort zurück."""

    def __init__(self, payload, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("FEATURE_INBOX_") or k.startswith("SMTP_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("FEATURE_INBOX_STATE", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("FEATURE_INBOX_REPORT_TO", "adrian@example.org")
    monkeypatch.setenv("FEATURE_INBOX_SMTP_FROM", "inbox@example.org")
    return Config.from_env()


@pytest.fixture
def state(cfg):
    return State(cfg.state_path)
