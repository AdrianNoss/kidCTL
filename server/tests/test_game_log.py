"""Protokoll-Reiter „Spiele“: Sitzungen aus „spielt gerade“, Filter, Meistgespielt, Löschung."""

from datetime import datetime, timedelta, timezone

from kidctl_common import protocol as p

from app import blocklog, nowplaying
from app.db import SessionLocal
from app.models import GameSession
from test_policy import _policy, _setup, device
from test_programs import _sync

CP = p.PlayingEntry(username="max", key="game:heroic:gog:1423049311", name="Cyberpunk 2077")
PORTAL = p.PlayingEntry(username="max", key="game:steam:620", name="Portal 2")


def test_sessions_from_reports(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        _policy(ws)
        # über WebSocket: legt eine Sitzung an
        ws.send_text(p.AppUsageReport(entries=[], playing=[CP]).model_dump_json())
        _sync(ws)
        t0 = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0) - timedelta(hours=3)
        with SessionLocal() as db:
            db.query(GameSession).delete()
            # minütliche Meldungen: 0..40 min Cyberpunk, Lücke > 3 min, dann neue Sitzung; Portal parallel
            for m in list(range(0, 41)) + [50, 51]:
                nowplaying.record_sessions(db, dev_id, [CP], t0 + timedelta(minutes=m))
            nowplaying.record_sessions(db, dev_id, [PORTAL, PORTAL,
                                                    p.PlayingEntry(username="fremd", key="game:steam:1", name="X"),
                                                    p.PlayingEntry(username="max", key="exe:/usr/bin/x", name="Y")],
                                       t0 + timedelta(minutes=5))
            db.commit()
            rows = db.query(GameSession).order_by(GameSession.started_at).all()
            assert [(r.name, blocklog.session_minutes(r)) for r in rows] == [
                ("Cyberpunk 2077", 41), ("Portal 2", 1), ("Cyberpunk 2077", 2)]

        page = client.get(f"/children/{cid}/protokoll?kind=game").text
        assert "3 Sitzungen" in page and "Meistgespielt" in page and "Status" not in page.split("log-summary")[0]
        top = page.split("Meistgespielt")[1].split("</table>")[0]
        assert top.index("Cyberpunk") < top.index("Portal") and "0:43 h" in top and "Heroic" in top
        assert "–" in page and "läuft ·" not in page                      # beendet, mit Zeitspanne
        assert "1 Sitzung" in client.get(f"/children/{cid}/protokoll?kind=game&q=portal").text
        # Sperr-Ansicht unberührt
        assert "Sitzung" not in client.get(f"/children/{cid}/protokoll").text.split("log-summary")[1][:80]

        # laufende Sitzung
        with SessionLocal() as db:
            nowplaying.record_sessions(db, dev_id, [PORTAL], datetime.now(timezone.utc).replace(tzinfo=None))
            db.commit()
        assert "läuft ·" in client.get(f"/children/{cid}/protokoll?kind=game&range=1").text


def test_sessions_pruned_after_90_days(client, parent):
    cid, tok = _setup(client, parent)
    now = datetime(2026, 10, 19, 12)
    with SessionLocal() as db:
        db.add(GameSession(child_id=cid, username="max", key="game:steam:1", name="Alt",
                           started_at=now - timedelta(days=95), last_seen_at=now - timedelta(days=91)))
        db.add(GameSession(child_id=cid, username="max", key="game:steam:2", name="Neu",
                           started_at=now - timedelta(days=95), last_seen_at=now - timedelta(days=89)))
        db.commit()
        assert blocklog.prune(db, now=now) == 1
        assert [s.name for s in db.query(GameSession)] == ["Neu"]
