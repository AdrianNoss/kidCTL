"""Tray-App: Katalog vollständig, Sprachwahl aus der Sitzung, englische Texte."""

from datetime import datetime, time, timezone
from pathlib import Path

from kidctl_common import i18n
from kidctl_common import tray as t
from kidctl_common.i18n_check import extract_python, problems, unused

from kidctl_tray import texts

PKG = Path(__file__).resolve().parents[1] / "kidctl_tray"
PO = PKG / "locale" / "en.po"


def _msgids():
    return extract_python(list(PKG.rglob("*.py")))


def test_every_text_has_english_translation():
    assert problems(PO, _msgids()) == []


def test_no_stale_translations():
    assert unused(PO, _msgids()) == []


def test_language_from_session():
    try:
        assert texts.setup_language({"LANG": "en_US.UTF-8"}) == "en"
        st = t.TrayState(restricted=True, allowed=True, remaining_seconds=80 * 60, used_seconds=40 * 60,
                         allowed_minutes=120, window_start=time(8), window_end=time(20), online=False,
                         game_limit_minutes=60, game_used_seconds=0)
        assert texts.detail_lines(st) == ["1 h 20 min left (today until 20:00)", "Used today: 40 min of 2 h",
                                          "Allowed: 08:00–20:00", "Game time: 1 h of 1 h left",
                                          "Offline – messages will be sent once connected"]
        item = t.BlockedItem(access_kind="domain", key="domain:a.de", name="a.de",
                             last_at=datetime(2026, 10, 20, 12, 3, tzinfo=timezone.utc), count=2)
        assert texts.blocked_line(item).startswith("Website · last 20 Oct, ") and texts.blocked_line(item).endswith("· 2×")
        assert texts.setup_language({"LANG": "de_DE.UTF-8"}) == "de"
        assert texts.status_line(st) == "Noch 1:20 h (heute bis 20:00 Uhr)"
    finally:
        i18n.set_current("de")
