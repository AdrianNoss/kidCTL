"""Integritätsmeldungen: Speichern, Alarm nur bei neuen Fehlern, Anzeige, Migration."""

from sqlalchemy import select

from kidctl_common import protocol as p

from app import integrity
from app.db import SessionLocal
from app.models import Device

from test_policy import _policy, _setup, device
from test_programs import _sync


def report(**states):
    return p.IntegrityReport(checks=[p.IntegrityCheck(id=k, label=f"Prüfung {k}", ok=v,
                                                      detail="" if v else f"{k} kaputt")
                                     for k, v in states.items()])


def test_alarm_only_for_new_failures(client, parent, monkeypatch):
    alarms = []
    monkeypatch.setattr(integrity.push, "notify_parents",
                        lambda title, body, url="/", tag=None: alarms.append((title(), body(), url)))
    cid, tok = _setup(client, parent)
    with device(client, tok, "Laptop Max", map_to=cid) as (ws, dev_id):
        _policy(ws)
        ws.send_text(report(pam=True, nsswitch=True).model_dump_json())
        _sync(ws)
        assert alarms == []
        page = client.get(f"/devices/{dev_id}").text
        assert "alle Schutzmaßnahmen intakt" in page and "Prüfung pam" in page

        ws.send_text(report(pam=False, nsswitch=True).model_dump_json())
        _sync(ws)
        assert alarms == [("Integritätsfehler auf „Laptop Max“", "Prüfung pam", f"/devices/{dev_id}")]
        ws.send_text(report(pam=False, nsswitch=True).model_dump_json())   # weiterhin kaputt
        ws.send_text(report(pam=False, nsswitch=False).model_dump_json())  # zusätzlicher Fehler
        _sync(ws)
        assert [a[1] for a in alarms] == ["Prüfung pam", "Prüfung nsswitch"]
        assert "pam kaputt" in client.get(f"/devices/{dev_id}").text
        assert "Integritätsfehler" in client.get("/devices").text and "Integritätsfehler" in client.get("/").text

        ws.send_text(report(pam=True, nsswitch=True).model_dump_json())    # behoben
        _sync(ws)
        ws.send_text(report(pam=False, nsswitch=True).model_dump_json())   # erneut -> neuer Alarm
        _sync(ws)
        assert len(alarms) == 3
        with SessionLocal() as db:
            assert [c["id"] for c in db.get(Device, dev_id).integrity_failed] == ["pam"]
