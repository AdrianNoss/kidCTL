"""Programmkontrolle unter Windows (Klassifizierung, Katalog, Watcher, ACL) mit Attrappen."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kidctl_common import protocol as p

from kidctl_agent import store
from kidctl_agent.win import acl, catalog
from kidctl_agent.win.procs import WinProc
from kidctl_agent.win.programs import WinAllowed, app_root, classify
from kidctl_agent.win.watcher import WinProgramWatcher

KID = "S-1-5-21-1-2-3-1002"
W = r"C:\Windows"


def proc(exe, cmd="", pfn="", sid=KID, pid=100, start=1):
    return WinProc(pid=pid, ppid=4, exe=exe, session=2, sid=sid, start=start, cmdline=cmd, pfn=pfn)


def allowed(*keys, **kw):
    kw.setdefault("catalog_exes", {catalog.norm(W + r"\System32\Taskmgr.exe"), catalog.norm(W + r"\explorer.exe")})
    kw.setdefault("catalog_pfns", {"Microsoft.WindowsCalculator_8wekyb3d8bbwe", "windows.immersivecontrolpanel_cw5n1h2txyewy"})
    kw.setdefault("known_pfns", kw["catalog_pfns"] | {"Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"})
    return WinAllowed(set(keys), sid=KID, install_dir=r"C:\Program Files\KidCTL", **kw)


def test_windows_base_and_tools():
    a = allowed()
    assert classify(proc(W + r"\explorer.exe"), a) is None                       # Shell trotz Startmenü-Eintrag
    assert classify(proc(W + r"\System32\RuntimeBroker.exe"), a) is None
    assert classify(proc(W + r"\System32\Taskmgr.exe"), a).code == "system_tool"   # nur mit Freigabe
    cm = allowed(catalog_exes={catalog.norm(W + r"\System32\cleanmgr.exe")})
    assert classify(proc(W + r"\System32\cleanmgr.exe"), cm, parent_exe=lambda: W + r"\explorer.exe"
                    ).code == "not_allowed"                                       # Startmenü -> Eltern
    assert classify(proc(W + r"\System32\cleanmgr.exe", "cleanmgr /autocleanstoragesense"), cm,
                    parent_exe=lambda: W + r"\System32\taskhostw.exe") is None   # geplante Aufgabe
    assert classify(proc(W + r"\System32\Taskmgr.exe"), cm, parent_exe=lambda: W + r"\System32\svchost.exe"
                    ).code == "system_tool"                                       # Werkzeuge nie
    assert classify(proc(r"C:\ProgramData\Microsoft\Windows Defender\Platform\4.18\DefenderSessionHelper.exe"),
                    cm) is None
    assert classify(proc(W + r"\System32\WindowsPowerShell\v1.0\powershell.exe"), a).code == "system_tool"
    assert classify(proc(W + r"\System32\reg.exe", "reg add HKCU\\x"), a).code == "system_tool"
    assert classify(proc(W + r"\System32\cmd.exe", 'C:\\Windows\\system32\\cmd.exe /d /c "dir"'), a) is None
    assert classify(proc(W + r"\System32\cmd.exe", "cmd.exe"), a).code == "interactive_shell"
    assert classify(proc(W + r"\System32\cmd.exe", "cmd /k echo"), a).code == "interactive_shell"
    assert classify(proc(W + r"\System32\rundll32.exe", r"rundll32.exe C:\Windows\System32\shell32.dll,X"), a) is None
    v = classify(proc(W + r"\System32\rundll32.exe", r'rundll32 "C:\Users\kind\Downloads\evil.dll",Run'), a)
    assert v.code == "foreign_dll" and not v.grantable
    assert classify(proc(r"C:\Program Files\KidCTL\python\pythonw.exe"), a) is None
    assert classify(proc(r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\1\msedgewebview2.exe"), a) is None


def test_released_programs_and_folders():
    a = allowed(r"exe:C:\Program Files\Mozilla Firefox\firefox.exe", r"exe:C:\Windows\System32\Taskmgr.exe",
                r"exe:C:\Windows\System32\cmd.exe")
    assert classify(proc(r"C:\PROGRAM FILES\Mozilla Firefox\firefox.exe"), a) is None   # Groß/klein egal
    assert classify(proc(r"C:\Program Files\Mozilla Firefox\pingsender.exe"), a) is None  # Programmordner
    assert classify(proc(r"C:\Program Files\Other\x.exe"), a).key == r"exe:C:\Program Files\Other\x.exe"
    assert classify(proc(W + r"\System32\Taskmgr.exe"), a) is None
    assert classify(proc(W + r"\System32\cmd.exe", "cmd.exe"), a) is None       # freigegebene Shell interaktiv
    assert classify(proc(W + r"\System32\reg.exe"), a).code == "system_tool"    # kein Ordner unter %WINDIR%


def test_own_files_blocked_except_released_app_folder():
    roblox = r"C:\Users\kind\AppData\Local\Roblox\Versions\version-2\RobloxPlayerBeta.exe"
    owner = lambda path: KID   # noqa: E731 – vom Kind geladen/entpackt
    assert classify(proc(r"C:\Users\kind\Downloads\game.exe"), allowed(), owner).code == "own_program"
    assert classify(proc(W + r"\Temp\x.exe"), allowed(), owner).code == "own_program"   # beschreibbar unter %WINDIR%
    a = allowed(r"exe:C:\Users\kind\AppData\Local\Roblox\Versions\version-1\RobloxPlayerLauncher.exe")
    assert classify(proc(roblox), a, owner) is None                     # neuer Versionsordner, gleicher App-Ordner
    assert app_root(roblox) == r"c:\users\kind\appdata\local\roblox"


def test_store_apps():
    a = allowed("appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe")
    calc = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_1_x64__8wekyb3d8bbwe\CalculatorApp.exe"
    assert classify(proc(calc, pfn="Microsoft.WindowsCalculator_8wekyb3d8bbwe"), a) is None
    v = classify(proc(W + r"\ImmersiveControlPanel\SystemSettings.exe", pfn="windows.immersivecontrolpanel_cw5n1h2txyewy"), a)
    assert v.key == "appx:windows.immersivecontrolpanel_cw5n1h2txyewy"
    assert classify(proc(W + r"\SystemApps\X\StartMenuExperienceHost.exe",
                         pfn="Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"), a) is None
    v = classify(proc(r"C:\Program Files\WindowsApps\New_1__abc\new.exe", pfn="Neu.App_0123456789abc"), a)
    assert v.unknown_package
    # Hintergrundaufgabe einer nicht freigegebenen App, Shell-Teile im Funktionspaket
    assert classify(proc(W + r"\System32\backgroundTaskHost.exe", pfn="Microsoft.WindowsCalculator_8wekyb3d8bbwe"),
                    allowed()) is None
    assert classify(proc(W + r"\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\CrossDeviceResume.exe",
                         pfn="MicrosoftWindows.Client.CBS_cw5n1h2txyewy"), allowed()) is None


def test_catalog_parse():
    data = {"lnk": [
        {"name": "Firefox", "target": r"C:\Program Files\Mozilla Firefox\firefox.exe", "folder": "x"},
        {"name": "Firefox", "target": r"C:\program files\mozilla firefox\FIREFOX.EXE", "folder": "y"},  # doppelt
        {"name": "Firefox deinstallieren", "target": r"C:\Program Files\Mozilla Firefox\uninstall\helper.exe"},
        {"name": "Website", "target": "https://example.org"},
        {"name": "Task-Manager", "target": r"%windir%\system32\taskmgr.exe"},
        {"name": "Weg", "target": r"C:\gibts\nicht.exe"},
        {"name": "KidCTL", "target": r"C:\Program Files\KidCTL\python\pythonw.exe"}],
        "appx": [{"pfn": "Microsoft.WindowsCalculator_8wekyb3d8bbwe", "name": "Rechner", "path": "C:\\x", "entry": True},
                 {"pfn": "Microsoft.Windows.ShellExperienceHost_cw5n1h2txyewy", "name": "", "entry": False},
                 {"pfn": "Microsoft.MinecraftUWP_8wekyb3d8bbwe", "name": "ms-resource:x", "entry": True, "game": True},
                 {"pfn": "MicrosoftWindows.Client.CBS_cw5n1h2txyewy", "name": "Funktionspaket", "entry": True},
                 {"pfn": "kaputt; rm", "entry": True}]}
    exists = lambda path: "gibts" not in path   # noqa: E731
    cat = catalog.parse(data, exists=exists, skip_dir=r"C:\Program Files\KidCTL")
    assert not any("kidctl" in a.key.lower() or "CBS" in a.key for a in cat.apps)
    keys = {a.key: a for a in cat.apps}
    assert r"exe:C:\Program Files\Mozilla Firefox\firefox.exe" in keys
    assert keys[r"exe:C:\Program Files\Mozilla Firefox\firefox.exe"].categories == ["Network", "WebBrowser"]
    assert len([k for k in keys if "firefox" in k.lower()]) == 1
    assert any(k.lower().endswith("taskmgr.exe") and keys[k].categories == ["Utility"] or k.lower().endswith("taskmgr.exe")
               for k in keys)
    assert keys["appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe"].name == "Rechner"
    assert keys["appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe"].name_en == "Calculator"
    assert catalog.readable("Microsoft.BingWeather") == "Bing Weather"
    assert keys["appx:Microsoft.MinecraftUWP_8wekyb3d8bbwe"].categories == ["Game"]
    assert keys["appx:Microsoft.MinecraftUWP_8wekyb3d8bbwe"].name == "Minecraft"
    assert cat.pfns == {"Microsoft.WindowsCalculator_8wekyb3d8bbwe", "Microsoft.MinecraftUWP_8wekyb3d8bbwe"}
    assert "Microsoft.Windows.ShellExperienceHost_cw5n1h2txyewy" in cat.known_pfns
    assert not any("uninstall" in k for k in keys) and not any("nicht.exe" in k for k in keys)


# ---------- Watcher ----------

class FakeProcs:
    def __init__(self, table):
        self.table = table      # pid -> WinProc
        self.killed = []

    def snapshot(self):
        return {pid: 4 for pid in self.table}

    def read(self, pid, ppid=0):
        return self.table.get(pid)

    def terminate(self, pid):
        self.killed.append(pid)
        self.table.pop(pid, None)
        return True

    def file_owner(self, path):
        return KID if "Downloads" in path else "S-1-5-32-544"


class FakeAgent:
    def __init__(self, mode="enforce", programs=()):
        self.policy = p.Policy(version="v", timezone="Europe/Berlin", users={"kind": 1}, children=[{
            "id": 1, "name": "Lisa", "program_mode": mode, "allowed_programs": list(programs),
            "rules": [{"weekday": d, "quota_minutes": 600, "window_start": "00:00", "window_end": "23:59"}
                      for d in range(7)]}])
        self.accounts = SimpleNamespace(by_wire=lambda u: SimpleNamespace(sid=KID) if u == "kind" else None)
        self.notified = []
        self.desktop = SimpleNamespace(notify=self._notify)
        self._sessions = []
        self.online = False

    async def _notify(self, user, uid, title, body, critical=False, on_action=None, action=""):
        self.notified.append((user, title, body, action))

    def now_local(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin"))

    def decisions(self, now):
        return {"kind": SimpleNamespace(allowed=True)}

    def lang_of(self, user):
        return "de"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)


def _catalog():
    return catalog.Catalog(apps=[p.AppEntry(key=r"exe:C:\Program Files\Spiel\spiel.exe", name="Spiel"),
                                 p.AppEntry(key=r"exe:C:\WINDOWS\system32\taskmgr.exe", name="Task-Manager")],
                           pfns=set(), known_pfns=set())


def test_watcher_blocks_reports_and_remembers(state):
    async def main():
        agent = FakeAgent()
        table = {10: proc(r"C:\Program Files\Spiel\spiel.exe", pid=10),
                 11: proc(W + r"\System32\notepad.exe", pid=11),
                 12: proc(r"C:\Program Files\Spiel\spiel.exe", pid=12, sid="S-1-5-21-1-2-3-1001"),   # Papa
                 13: proc(r"C:\Users\kind\Downloads\x.exe", pid=13),
                 14: proc(r"C:\Windows\System32\Taskmgr.exe", pid=14)}
        fp = FakeProcs(table)
        w = WinProgramWatcher(agent, procs=fp, scan_catalog=_catalog, clock=lambda: 1000.0)
        await w.refresh_catalog()
        await w.poll()
        assert sorted(fp.killed) == [10, 13, 14]
        assert agent.notified[0][1] == "Programm gesperrt" and "Spiel" in agent.notified[0][2]
        assert agent.notified[0][3] == "Freigabe anfragen"
        assert {e.key for e in w.pending} == {r"exe:C:\Program Files\Spiel\spiel.exe", r"exe:C:\Users\kind\Downloads\x.exe",
                                              r"exe:C:\WINDOWS\system32\taskmgr.exe"}   # Schreibweise des Katalogs
        assert "Task-Manager" in {e.name for e in w.pending}
        assert {i.name for i in w.recent_items("kind")} == {"Spiel", "x.exe", "Task-Manager"}
        sent = []
        conn = SimpleNamespace(connected=True, send=lambda m: _async_true(sent, m))
        await w.flush(conn)
        assert len(sent[0].events) == 3 and w.pending == []
    asyncio.run(main())


async def _async_true(sent, m):
    sent.append(m)
    return True


def test_watcher_audit_and_release(state):
    async def main():
        agent = FakeAgent(mode="audit")
        fp = FakeProcs({10: proc(r"C:\Program Files\Spiel\spiel.exe", pid=10)})
        w = WinProgramWatcher(agent, procs=fp, scan_catalog=_catalog, clock=lambda: 1000.0)
        await w.refresh_catalog()
        await w.poll()
        assert fp.killed == [] and w.pending[0].audit and agent.notified == []
        agent2 = FakeAgent(programs=[r"exe:C:\Program Files\Spiel\spiel.exe"])
        fp2 = FakeProcs({10: proc(r"C:\Program Files\Spiel\spiel.exe", pid=10)})
        w2 = WinProgramWatcher(agent2, procs=fp2, scan_catalog=_catalog, clock=lambda: 1000.0)
        await w2.refresh_catalog()
        await w2.scan()
        assert fp2.killed == []
    asyncio.run(main())


def test_watcher_without_catalog_only_audits(state):
    async def main():
        agent = FakeAgent()
        fp = FakeProcs({10: proc(r"C:\Program Files\Spiel\spiel.exe", pid=10)})

        def broken():
            raise OSError("PowerShell kaputt")
        w = WinProgramWatcher(agent, procs=fp, scan_catalog=broken, clock=lambda: 1000.0)
        await w.refresh_catalog()
        await w.poll()
        assert fp.killed == [] and w.pending[0].audit   # Sicherung
    asyncio.run(main())


# ---------- ACL ----------

def test_acl_manager(state, tmp_path):
    calls = []

    def run(args, **kw):
        calls.append(args[1:-2])
        return SimpleNamespace(returncode=0, stdout=b"")
    prof = tmp_path / "kind"
    app = prof / "AppData" / "Local" / "Roblox"
    app.mkdir(parents=True)
    shared = tmp_path / "Public"
    shared.mkdir()
    m = acl.AclManager(store, run=run, profile=lambda sid: str(prof) if sid == KID else None, shared=(str(shared),))
    m.sync({KID: [str(app)]})
    assert [str(prof), "/deny", f"*{KID}:(OI)(IO)(X)", f"*{KID}:(OI)(CI)(WDAC)"] in calls
    assert [str(prof), "/grant", "*S-1-3-4:(OI)(CI)(M)"] in calls
    assert [str(app), "/grant", f"*{KID}:(OI)(IO)(RX)"] in calls
    assert [str(shared), "/deny", f"*{KID}:(OI)(IO)(X)", f"*{KID}:(OI)(CI)(WDAC)"] in calls
    calls.clear()
    m2 = acl.AclManager(store, run=run, profile=lambda sid: str(prof), shared=(str(shared),))   # Neustart
    m2.sync({KID: [str(app)]})
    assert calls == []                                    # nichts doppelt
    m2.sync({})
    assert [str(prof), "/remove:d", f"*{KID}"] in calls and [str(app), "/remove:g", f"*{KID}"] in calls
    assert [str(prof), "/grant", f"*{KID}:(OI)(CI)(WDAC)"] in calls          # Vollzugriff wiederhergestellt
    assert [str(shared), "/grant", f"*{KID}:(OI)(CI)(WDAC)"] not in calls
    assert [str(shared), "/remove:g", "*S-1-3-4"] in calls
    assert store.load("winacl.json") == {"profiles": {}, "shared": {str(shared): []}, "dirs": {}}


def test_acl_protected_sddl():
    sddl = (f"D:AI(D;OIIO;0x20;;;{KID})(D;OICI;WD;;;{KID})(A;OICIID;FA;;;SY)(A;OICIID;FA;;;{KID})"
            "(A;OICI;0x1301bf;;;OW)")
    assert acl.protected(sddl, KID)
    assert not acl.protected("D:AI(A;OICIID;FA;;;SY)", KID)
    assert not acl.protected(f"D:(D;OICI;WD;;;{KID})", KID)


def test_acl_extra_dirs(state, tmp_path):
    calls = []

    def run(args, **kw):
        calls.append(args[1:-2])
        return SimpleNamespace(returncode=0, stdout=b"")
    prof, steam = tmp_path / "kind", tmp_path / "Steam"
    prof.mkdir()
    steam.mkdir()
    m = acl.AclManager(store, run=run, profile=lambda sid: str(prof), shared=())
    m.sync({KID: []}, {KID: [str(steam)]})
    assert [str(steam), "/deny", f"*{KID}:(OI)(IO)(X)", f"*{KID}:(OI)(CI)(WDAC)"] in calls
    calls.clear()
    m.sync({KID: []}, {KID: []})          # Steam freigegeben -> Verbot weg
    assert [str(steam), "/remove:d", f"*{KID}"] in calls and [str(prof), "/remove:d", f"*{KID}"] not in calls
