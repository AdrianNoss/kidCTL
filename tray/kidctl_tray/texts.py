"""Anzeigetexte und Formatierung (ohne Qt, testbar)."""

from __future__ import annotations

from datetime import datetime, timezone

from kidctl_common import tray as t
from kidctl_common.protocol import ChatMessage

REASON = {
    "quota": "Bildschirmzeit für heute aufgebraucht",
    "window_not_open": "Die erlaubte Zeit hat noch nicht begonnen",
    "window_closed": "Die erlaubte Zeit ist für heute vorbei",
    "locked": "Von deinen Eltern gesperrt",
}


def fmt_duration(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}:{minutes % 60:02d} h"


def icon_text(state: t.TrayState) -> str:
    """Kurztext im Tray-Symbol (max. 3 Zeichen)."""
    if not state.allowed:
        return "×"
    minutes = state.remaining_seconds // 60
    return str(minutes) if minutes < 100 else f"{minutes // 60}h"


def level(state: t.TrayState) -> str:
    if not state.allowed or state.remaining_seconds <= 5 * 60:
        return "critical"
    if state.remaining_seconds <= 15 * 60:
        return "warning"
    return "ok"


def status_line(state: t.TrayState) -> str:
    if not state.allowed:
        return REASON.get(state.reason or "", "Keine Bildschirmzeit")
    text = f"Noch {fmt_duration(state.remaining_seconds)}"
    if state.window_end:
        text += f" (heute bis {state.window_end:%H:%M} Uhr)"
    return text


def detail_lines(state: t.TrayState) -> list[str]:
    lines = [status_line(state),
             f"Heute genutzt: {fmt_duration(state.used_seconds)} von {fmt_duration(state.allowed_minutes * 60)}"]
    if state.window_start and state.window_end:
        lines.append(f"Erlaubt: {state.window_start:%H:%M}–{state.window_end:%H:%M} Uhr")
    if state.game_limit_minutes is not None:
        left = max(0, state.game_limit_minutes * 60 - state.game_used_seconds)
        lines.append(f"Spielzeit: noch {fmt_duration(left)} von {fmt_duration(state.game_limit_minutes * 60)}"
                     if left else "Spielzeit für heute aufgebraucht")
    if not state.online:
        lines.append("Offline – Nachrichten werden gesendet, sobald Verbindung besteht")
    elif state.pending_outbox:
        lines.append(f"{state.pending_outbox} Nachricht(en) werden gesendet …")
    return lines


def local_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d.%m. %H:%M")


def request_summary(m: ChatMessage) -> str:
    if m.request_target == "games":
        return f"+{m.requested_minutes or 0} min Spielzeit"
    parts = []
    if m.requested_minutes:
        parts.append(f"+{m.requested_minutes} min")
    if m.requested_window_end:
        parts.append(f"bis {m.requested_window_end:%H:%M} Uhr")
    return " und ".join(parts)


def request_status(m: ChatMessage) -> str:
    if m.status == "approved":
        parts = []
        if m.approved_minutes:
            parts.append(f"+{m.approved_minutes} min")
        if m.approved_window_end:
            parts.append(f"bis {m.approved_window_end:%H:%M} Uhr")
        return "✔ Genehmigt" + (f": {' und '.join(parts)}" if parts else "")
    return {"pending": "⏳ Wartet auf Antwort", "rejected": "✘ Abgelehnt",
            "expired": "Verfallen (Tag vorbei)"}.get(m.status or "", "")


def kind_label(kind: str | None) -> str:
    return "Website" if kind == "domain" else "Programm"


def access_status(m: ChatMessage) -> str:
    if m.status == "approved":
        return "✔ Heute erlaubt" if m.access_scope == "today" else "✔ Dauerhaft erlaubt"
    return {"pending": "⏳ Wartet auf Antwort", "rejected": "✘ Abgelehnt"}.get(m.status or "", "")


def blocked_line(item: t.BlockedItem) -> str:
    """„Website · zuletzt 14:03 · 3×“"""
    parts = [kind_label(item.access_kind), f"zuletzt {local_time(item.last_at)}"]
    if item.count > 1:
        parts.append(f"{item.count}×")
    return " · ".join(parts)
