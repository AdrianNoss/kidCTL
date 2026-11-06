"""Windows-Clients: Plattform je Gerät, Windows-Installer im Repo, Update-Hinweise, Migration."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select, text

from kidctl_common import protocol as p

from app import alarms, migrate, versions
from app.db import SessionLocal
from app.models import Device, utcnow
from conftest import login, recv
from test_smoke import _pair


def test_device_platform_migrated_on_old_schema(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/alt.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
        c.execute(text("INSERT INTO device (name) VALUES ('alt')"))
    migrate.run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT platform FROM device")).scalar() == "linux"


def test_windows_hello_and_users(client, parent):
    login(client, parent)
    code = _pair(client)
    enrolled = p.EnrollResponse.model_validate(client.post(p.ENROLL_PATH, json={"code": code, "hostname": "LISA-PC"}).json())
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {enrolled.token}"}) as ws:
        ws.send_text(p.Hello(protocol_version=p.PROTOCOL_VERSION, agent_version="0.29.0", hostname="LISA-PC",
                             platform="windows").model_dump_json())
        assert isinstance(recv(ws), p.Welcome)
        ws.send_text(p.ReportUsers(users=[p.LocalUser(username="Lisa", uid=1002, full_name="Lisa (lisa@outlook.de)"),
                                          p.LocalUser(username="rid-1003", uid=1003, full_name="Max Mustermann")]
                                   ).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(recv(ws), p.Pong)
        page = client.get(f"/devices/{enrolled.device_id}").text
    assert "Windows" in page and "lisa@outlook.de" in page and "rid-1003" in page
    with SessionLocal() as db:
        assert db.get(Device, enrolled.device_id).platform == "windows"
    # Ältere Agenten senden kein platform -> linux
    assert p.Hello.model_validate({"type": "hello", "protocol_version": 1, "agent_version": "0.28.0",
                                   "hostname": "x"}).platform == "linux"


def _windows_repo(tmp_path, *names, age_days=2):
    folder = tmp_path / "repo" / "windows"
    folder.mkdir(parents=True)
    t = (datetime.now() - timedelta(days=age_days)).timestamp()
    for n in names:
        (folder / n).write_text("")
        os.utime(folder / n, (t, t))
    os.utime(folder, (1, 1))
    return tmp_path / "repo"


def test_latest_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, "_win_cache", None)
    repo = _windows_repo(tmp_path, "KidCTL-Setup-0.29.0.exe", "KidCTL-Setup-0.29.10.exe", "latest.json",
                         "latest.json.sig", "KidCTL-Setup-0.30.0.exe.part")
    assert versions.latest_windows(repo).version == "0.29.10"
    assert versions.latest(repo) is None              # Windows zählt nicht für Linux
    assert versions.latest_windows(tmp_path / "fehlt") is None


def test_update_hints_per_platform(client, parent, monkeypatch):
    lin = versions.Release("0.28.0", utcnow() - timedelta(days=2))
    win = versions.Release("0.29.0", utcnow() - timedelta(days=2))
    monkeypatch.setattr(versions, "latest", lambda repo=None: lin)
    monkeypatch.setattr(versions, "latest_windows", lambda repo=None: win)
    with SessionLocal() as db:
        db.add_all([Device(name="Arch", token_hash="c" * 64, agent_version="0.28.0"),
                    Device(name="Win", token_hash="d" * 64, agent_version="0.28.0", platform="windows")])
        db.commit()
        due = alarms.due_update_alarms(db)
        assert [(d.name, v) for d, v in due] == [("Win", "0.29.0")]   # Linux ist aktuell
        db.commit()
        win_id = db.scalar(select(Device.id).where(Device.name == "Win"))
    login(client, parent)
    page = client.get(f"/devices/{win_id}").text
    assert "Update verfügbar: 0.29.0. Wird automatisch installiert" in page and "pacman" not in page
    assert "Update: 0.28.0 → 0.29.0" in client.get("/devices").text


def test_windows_program_keys():
    from app import access, programs
    assert access.valid_key("program", r"exe:C:\Program Files\Mozilla Firefox\firefox.exe") == \
        r"exe:C:\Program Files\Mozilla Firefox\firefox.exe"
    assert access.valid_key("program", "appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe")
    assert access.valid_key("program", "appx:kaputt") is None
    assert access.valid_key("program", r"exe:C:\Programme\..\Windows\x.exe") is None
    assert access.valid_key("program", "exe:/usr/bin/git") == "exe:/usr/bin/git"
    assert programs.custom_key(r"C:\Tools\spiel.exe") == r"exe:C:\Tools\spiel.exe"
    for bad in (r"C:\Tools\spiel.bat", "C:\\Tools\\a|b.exe", "C:\\Tools\\", r"C:\Tools\..\x.exe"):
        try:
            programs.custom_key(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_windows_programs_in_webui(client, parent):
    from app.models import Child, ChildProgram
    from conftest import csrf_from
    login(client, parent)
    client.post("/children", data={"csrf_token": csrf_from(client.get("/children").text), "name": "Lisa"})
    with SessionLocal() as db:
        cid = db.scalar(select(Child.id).where(Child.name == "Lisa"))
    page = client.get(f"/children/{cid}").text
    r = client.post(f"/children/{cid}/programs", data={"csrf_token": csrf_from(page), "program_mode": "enforce",
                                                        "app": ["appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe",
                                                                r"exe:C:\Program Files\Mozilla Firefox\firefox.exe"]})
    assert r.status_code in (200, 303)
    r = client.post(f"/children/{cid}/programs/custom", data={"csrf_token": csrf_from(page), "path": r"C:\Tools\spiel.exe",
                                                               "name": ""}, follow_redirects=True)
    assert r.status_code == 200 and "spiel.exe" in r.text
    with SessionLocal() as db:
        keys = {k for (k,) in db.query(ChildProgram.key).filter(ChildProgram.child_id == cid)}
    assert {"appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe", r"exe:C:\Program Files\Mozilla Firefox\firefox.exe",
            r"exe:C:\Tools\spiel.exe"} <= keys


def test_game_flag_in_webui_and_policy(client, parent):
    from app import policy as pol
    from app.models import Child, DeviceApp, DeviceUser
    from conftest import csrf_from
    login(client, parent)
    client.post("/children", data={"csrf_token": csrf_from(client.get("/children").text), "name": "Ben"})
    with SessionLocal() as db:
        cid = db.scalar(select(Child.id).where(Child.name == "Ben"))
        dev = Device(name="PC", token_hash="e" * 64, platform="windows")
        db.add(dev)
        db.flush()
        db.add_all([DeviceUser(device_id=dev.id, username="ben", uid=1004, child_id=cid),
                    DeviceApp(device_id=dev.id, key=r"exe:C:\Users\ben\AppData\Local\Roblox\Roblox.exe", name="Roblox",
                              categories=""),
                    DeviceApp(device_id=dev.id, key="appx:Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe",
                              name="Solitaire", categories="Game")])
        db.commit()
        dev_id = dev.id
    page = client.get(f"/children/{cid}").text
    assert 'name="game" value="appx:Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe" checked disabled' in page
    roblox = r"exe:C:\Users\ben\AppData\Local\Roblox\Roblox.exe"
    client.post(f"/children/{cid}/programs", data={"csrf_token": csrf_from(page), "program_mode": "enforce",
                                                    "app": [roblox], "game": [roblox, "kaputt"]})
    page = client.get(f"/children/{cid}").text
    assert f'name="game" value="{roblox}" checked>' in page
    with SessionLocal() as db:
        built = pol.build_policy(db, dev_id)
    assert next(c for c in built.children if c.id == cid).game_apps == [roblox]   # „kaputt“ verworfen
