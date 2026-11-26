"""Konfiguration ausschließlich über Umgebungsvariablen (``.env`` des Stacks)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(minimum, value)


@dataclass(frozen=True)
class Config:
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_inbox: str
    imap_done: str
    imap_unsorted: str
    github_repo: str
    github_token: str
    llm: str  # anthropic | none
    model: str
    interval_s: int
    state_path: str
    report_to: str
    report_hour: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_security: str  # starttls | ssl | none

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            imap_host=e("FEATURE_INBOX_IMAP_HOST", ""),
            imap_port=_int("FEATURE_INBOX_IMAP_PORT", 993, 1),
            imap_user=e("FEATURE_INBOX_IMAP_USER", ""),
            imap_password=e("FEATURE_INBOX_IMAP_PASSWORD", ""),
            imap_inbox=e("FEATURE_INBOX_IMAP_FOLDER", "INBOX"),
            imap_done=e("FEATURE_INBOX_IMAP_DONE", "Verarbeitet"),
            imap_unsorted=e("FEATURE_INBOX_IMAP_UNSORTED", "Unsortiert"),
            github_repo=e("FEATURE_INBOX_GITHUB_REPO", "AdrianNoss/kidCTL"),
            github_token=e("FEATURE_INBOX_GITHUB_TOKEN", ""),
            llm=e("FEATURE_INBOX_LLM", "anthropic").strip().lower(),
            model=e("FEATURE_INBOX_MODEL", "claude-haiku-5-5"),
            interval_s=_int("FEATURE_INBOX_INTERVAL_MIN", 15, 1) * 60,
            state_path=e("FEATURE_INBOX_STATE", "/data/feature-inbox/state.sqlite"),
            report_to=e("FEATURE_INBOX_REPORT_TO", ""),
            report_hour=min(23, _int("FEATURE_INBOX_REPORT_HOUR", 7, 0)),
            smtp_host=e("FEATURE_INBOX_SMTP_HOST", "") or e("SMTP_HOST", ""),
            smtp_port=_int("FEATURE_INBOX_SMTP_PORT", int(e("SMTP_PORT", "587") or 587), 1),
            smtp_user=e("FEATURE_INBOX_SMTP_USER", "") or e("SMTP_USER", ""),
            smtp_password=e("FEATURE_INBOX_SMTP_PASSWORD", "") or e("SMTP_PASSWORD", ""),
            smtp_from=e("FEATURE_INBOX_SMTP_FROM", "") or e("SMTP_FROM", ""),
            smtp_security=(e("FEATURE_INBOX_SMTP_SECURITY", "") or e("SMTP_SECURITY", "starttls")).strip().lower(),
        )

    def missing(self) -> list[str]:
        need = {
            "FEATURE_INBOX_IMAP_HOST": self.imap_host,
            "FEATURE_INBOX_IMAP_USER": self.imap_user,
            "FEATURE_INBOX_IMAP_PASSWORD": self.imap_password,
            "FEATURE_INBOX_GITHUB_TOKEN": self.github_token,
        }
        return [k for k, v in need.items() if not v]
