"""Spielzeit je Spiel und „spielt gerade“ (Agent meldet über app_usage_report)."""

from kidctl_common import protocol as p

from app import nowplaying, schedule as sch
from test_policy import _policy, _setup, device
from test_programs import _sync


def test_game_time_and_now_playing(client, parent, monkeypatch):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        _policy(ws)
        today = sch.today_local()
        ws.send_text(p.AppUsageReport(entries=[
            p.AppUsageEntry(username="max", day=today, key="game:heroic:gog:1423049311", seconds=4800,
                            name="Cyberpunk 2077\x1b[31m"),
            p.AppUsageEntry(username="max", day=today, key="exe:/usr/bin/heroic", seconds=5100),
            p.AppUsageEntry(username="max", day=today, key="game:steam:620", seconds=600, name="<b>Portal 2</b>"),
        ], playing=[p.PlayingEntry(username="max", key="game:heroic:gog:1423049311", name="Cyberpunk 2077"),
                    p.PlayingEntry(username="unbekannt", key="game:steam:1", name="Fremd")]).model_dump_json())
        _sync(ws)
        stats = client.get(f"/children/{cid}/stats").text
        assert "Cyberpunk 2077" in stats and "\x1b" not in stats and "Heroic" in stats
        assert "&lt;b&gt;Portal 2&lt;/b&gt;" in stats and "<b>Portal 2" not in stats       # escaped
        assert "1:20 h" in stats or "80 min" in stats or "1:20" in stats
        dash = client.get("/").text
        assert "▶ Cyberpunk 2077" in dash and "Fremd" not in dash
        assert "spielt gerade: Cyberpunk 2077" in client.get(f"/children/{cid}").text

        # Spiel beendet -> leere Liste beendet die Anzeige
        ws.send_text(p.AppUsageReport(entries=[], playing=[]).model_dump_json())
        _sync(ws)
        assert "▶" not in client.get("/").text
        # veraltete Meldung zählt nicht
        nowplaying.update(dev_id, [p.PlayingEntry(username="max", key="game:steam:620", name="Portal 2")])
        monkeypatch.setattr(nowplaying, "STALE_SECONDS", -1)
        assert "▶" not in client.get("/").text


def test_old_agent_report_without_playing_accepted():
    msg = p.agent_message_adapter.validate_json('{"type": "app_usage_report", "entries": []}')
    assert msg.playing == []
