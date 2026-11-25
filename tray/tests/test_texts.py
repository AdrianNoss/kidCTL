from datetime import datetime, time, timezone

from kidctl_common import tray as t
from kidctl_common.protocol import ChatMessage

from kidctl_tray import texts


def st(**kw):
    base = dict(restricted=True, child_name="Max", allowed=True, remaining_seconds=45 * 60,
                used_seconds=15 * 60, allowed_minutes=60, window_start=time(8), window_end=time(20),
                online=True)
    return t.TrayState(**{**base, **kw})


def test_level():
    assert texts.level(st()) == "ok"
    assert texts.level(st(remaining_seconds=10 * 60)) == "warning"
    assert texts.level(st(remaining_seconds=4 * 60)) == "critical"
    blocked = st(allowed=False, remaining_seconds=0, reason="quota")
    assert texts.level(blocked) == "critical"


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


def test_block_texts():
    from kidctl_common.protocol import Block
    school, dinner = Block(start=time(8), end=time(13), name="Schule"), Block(start=time(18), end=time(19))
    blocked = st(allowed=False, reason="blocked", block=school, blocks=[school, dinner])
    assert texts.status_line(blocked) == "Sperrzeit „Schule“ bis 13:00 Uhr"
    assert texts.status_line(st(allowed=False, reason="blocked", block=dinner)) == "Sperrzeit bis 19:00 Uhr"
    assert texts.status_line(st(allowed=False, reason="blocked")) == "Jetzt ist Sperrzeit"
    assert "Sperrzeiten: Schule 08:00–13:00, 18:00–19:00 Uhr" in texts.detail_lines(blocked)
    assert texts.status_line(st(until=time(18))) == "Noch 45 min (bis 18:00 Uhr)"


def test_free_line():
    """„Zählt nicht“: Hinweis direkt unter der Restzeit, nur solange Zeit erlaubt ist."""
    lines = texts.detail_lines(st(free=True))
    assert lines[1] == "Die Zeit läuft gerade nicht"
    assert "Die Zeit läuft gerade nicht" not in texts.detail_lines(st())
    assert "Die Zeit läuft gerade nicht" not in texts.detail_lines(st(free=True, allowed=False, reason="quota"))
