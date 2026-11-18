"""Lastmessung (nicht Teil der Suite; Aufruf: pytest -s tests/perf_admin_overview.py): 5.000 Familien, 20.000 Geräte, 10.000 Kinder; Antwortzeit der Übersicht."""
import time
from datetime import timedelta

from sqlalchemy import insert

from app import crypto
from app.models import Child, Device, Family, utcnow
from conftest import platform_db
from test_operators import admin_login, operator  # noqa: F401


def test_perf(client, operator, monkeypatch):  # noqa: F811
    from app import web
    from app.config import Settings
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)
    t0 = time.perf_counter()
    now = utcnow()
    with platform_db() as db:
        fams = [Family(name=f"Familie Nr {i}", plan="standard" if i % 3 else "free",
                       billing_status=["trial", "active", "past_due"][i % 3], trial_ends_at=now + timedelta(days=i % 40))
                for i in range(5000)]
        db.add_all(fams)
        db.commit()
        ids = [f.id for f in fams]
        for n, fid in enumerate(ids):
            with crypto.family(fid):
                db.execute(insert(Device), [{"family_id": fid, "name": f"PC{k}", "hostname": "", "token_hash": f"{fid:08d}{k:056d}",
                                             "agent_version": f"0.{30 + k}.0", "last_seen_at": now - timedelta(hours=k * 20),
                                             "integrity_json": ""} for k in range(4)])
                db.execute(insert(Child), [{"family_id": fid, "name": f"Kind{k}"} for k in range(2)])
        db.commit()
    print(f"\nAnlegen: {time.perf_counter() - t0:.1f} s")
    admin_login(client, operator)
    for url in ("/admin", "/admin?q=nr 4711", "/admin?sort=-activity&page=50", "/admin?status=trial&issue=offline&sort=name"):
        times = []
        for _ in range(3):
            t = time.perf_counter()
            r = client.get(url)
            times.append(time.perf_counter() - t)
            assert r.status_code == 200
        print(f"{url}: {min(times) * 1000:.0f} ms (min von 3)")

