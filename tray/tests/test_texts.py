from datetime import datetime, time, timezone

from kidctl_common import tray as t
from kidctl_common.protocol import ChatMessage

from kidctl_tray import texts


def st(**kw):
    base = dict(restricted=True, child_name="Max", allowed=True, remaining_seconds=45 * 60,
                used_seconds=15 * 60, allowed_minutes=60, window_start=time(8), window_end=time(20),
                online=True)
    return t.TrayState(**{**base, **kw})


def test_icon_text_and_level():
    assert texts.icon_text(st()) == "45" and texts.level(st()) == "ok"
    assert texts.icon_text(st(remaining_seconds=150 * 60)) == "2h"
    assert texts.level(st(remaining_seconds=10 * 60)) == "warning"
    assert texts.level(st(remaining_seconds=4 * 60)) == "critical"
    blocked = st(allowed=False, remaining_seconds=0, reason="quota")
    assert texts.icon_text(blocked) == "×" and texts.level(blocked) == "critical"


def test_status_and_details():
    assert texts.status_line(st()) == "Noch 45 min (heute bis 20:00 Uhr)"
    assert texts.status_line(st(allowed=False, reason="window_closed")) == "Die erlaubte Zeit ist für heute vorbei"
    lines = texts.detail_lines(st(online=False))
    assert "Heute genutzt: 15 min von 1:00 h" in lines and any("Offline" in x for x in lines)
    assert "2 Nachrichten werden gesendet …" in texts.detail_lines(st(pending_outbox=2))


def test_request_texts():
    m = ChatMessage(id=1, child_id=1, from_child=True, sender_name="Max", kind="extension_request",
                    body="bitte", created_at=datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc),
                    requested_minutes=30, requested_window_end=time(21), status="pending")
    assert texts.request_summary(m) == "+30 min und bis 21:00 Uhr"
    assert texts.request_status(m) == "⏳ Wartet auf Antwort"
    done = m.model_copy(update={"status": "approved", "approved_minutes": 20})
    assert texts.request_status(done) == "✔ Genehmigt: +20 min"
