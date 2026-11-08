"""Windows-Teil des Agenten mit Attrappen der Win32-Schicht (läuft unter Linux)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from kidctl_common import ed25519
from kidctl_common import protocol as p
from kidctl_common import tray as t

from kidctl_agent import store
from kidctl_agent.win import api
from kidctl_agent.win import update as upd
from kidctl_agent.win.accounts import Accounts, wire_name
from kidctl_agent.win.desktop import WinDesktop
from kidctl_agent.win.loginlock import WinLoginLock
from kidctl_agent.win.sessions import WinSessions


class FakeApi:
    """Lokale Konten und Sitzungen wie unter Windows."""
    UF_ACCOUNTDISABLE = api.UF_ACCOUNTDISABLE
    WTSActive = api.WTSActive
    WTS_SESSIONSTATE_LOCK = api.WTS_SESSIONSTATE_LOCK

    def __init__(self) -> None:
        # Name -> (voller Name, RID, Flags, Gruppen, E-Mail)
        self.users = {
            "Administrator": ("", 500, 0x2, ["Administratoren"], ""),
            "papa": ("Papa", 1001, 0, ["Administratoren", "Benutzer"], ""),
            "lisa": ("Lisa", 1002, 0, ["Benutzer"], "lisa@outlook.de"),
            "Max Mustermann": ("", 1003, 0, ["Benutzer"], ""),
        }
        self.sessions: list[tuple[int, int, str, int, int]] = []  # (ID, Zustand, User, Flags, Anmeldezeit)
        self.logoffs: list[int] = []
        self.messages: list[tuple[int, str]] = []
        self.set_calls: list[tuple[str, int]] = []
        self.fail_set: set[str] = set()

    def user_enum(self):
        return [(n, full, rid, flags) for n, (full, rid, flags, _g, _e) in self.users.items()]

    def account_sid(self, name):
        return f"S-1-5-21-1-2-3-{self.users[name][1]}"

    def user_internet_name(self, name):
        return self.users[name][4]

    def account_name(self, sid):
        assert sid == "S-1-5-32-544"
        return "Administratoren", "VORDEFINIERT"

    def user_local_groups(self, name):
        return self.users[name][3]

    def user_flags(self, name):
        return self.users[name][2]

    def set_user_flags(self, name, flags):
        if name in self.fail_set:
            raise OSError(5, "Zugriff verweigert")
        self.set_calls.append((name, flags))
        full, rid, _f, groups, mail = self.users[name]
        self.users[name] = (full, rid, flags, groups, mail)

    def enumerate_sessions(self):
        return [(0, 4)] + [(sid, state) for sid, state, *_ in self.sessions]

    def session_info(self, sid):
        for s, _state, user, flags, logon in self.sessions:
            if s == sid:
                return SimpleNamespace(UserName=user, SessionFlags=flags, LogonTime=logon)
        raise OSError(7022, "weg")

    def logoff_session(self, sid, wait):
        self.logoffs.append(sid)

    def kill_in_session(self, image, sid):
        self.killed = getattr(self, "killed", []) + [(image, sid)]

    def send_message(self, sid, title, body, timeout):
        self.messages.append((sid, title))
        return 1


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    return FakeApi()


def disabled(fake, name):
    return bool(fake.users[name][2] & api.UF_ACCOUNTDISABLE)


# ---------- Konten ----------

def test_wire_name():
    assert wire_name("lisa", 1002) == "lisa"
    assert wire_name("Max", 1004) == "Max"
    assert wire_name("Max Mustermann", 1003) == "rid-1003"
    assert wire_name("Jürgen", 1005) == "rid-1005"


def test_local_users_and_admins(fake):
    acc = Accounts(fake)
    users = {u.username: u for u in acc.local_users()}
    assert set(users) == {"papa", "lisa", "rid-1003"}          # RID < 1000 (Administrator) fehlt
    assert users["lisa"].uid == 1002 and users["lisa"].full_name == "Lisa (lisa@outlook.de)"
    assert users["rid-1003"].full_name == "Max Mustermann"
    assert acc.is_admin_wire("papa") and not acc.is_admin_wire("lisa")
    assert acc.by_name("LISA").wire == "lisa"                  # Windows: Groß-/Kleinschreibung egal


def test_admin_check_error_counts_as_admin(fake):
    acc = Accounts(fake)
    fake.user_local_groups = lambda name: (_ for _ in ()).throw(OSError(5, "x"))
    assert acc.is_admin_wire("lisa")   # im Zweifel nie aussperren


# ---------- Login-Sperre ----------

def test_loginlock_disables_and_restores(fake):
    lock = WinLoginLock(Accounts(fake), fake)
    lock.update({"lisa", "papa"})
    assert disabled(fake, "lisa") and not disabled(fake, "papa")   # Admin nie
    assert store.load("winlock.json") == {"disabled": ["S-1-5-21-1-2-3-1002"]}
    lock.update(set())
    assert not disabled(fake, "lisa")
    assert store.load("winlock.json") == {"disabled": []}


def test_loginlock_leaves_foreign_disabled_accounts(fake):
    fake.set_user_flags("Max Mustermann", api.UF_ACCOUNTDISABLE)   # von den Eltern deaktiviert
    fake.set_calls.clear()
    lock = WinLoginLock(Accounts(fake), fake)
    lock.update({"rid-1003"})
    lock.update(set())
    assert disabled(fake, "Max Mustermann") and fake.set_calls == []


def test_loginlock_survives_restart_and_release_all(fake):
    WinLoginLock(Accounts(fake), fake).update({"lisa"})
    lock = WinLoginLock(Accounts(fake), fake)       # Neustart: gemerkte Sperre
    assert lock.release_all() == 0 and not disabled(fake, "lisa")


def test_loginlock_redisables_manually_enabled_account(fake):
    lock = WinLoginLock(Accounts(fake), fake)
    lock.update({"lisa"})
    fake.set_user_flags("lisa", 0)     # jemand aktiviert es von Hand, Sperre gilt weiter
    lock.update({"lisa"})
    assert disabled(fake, "lisa")


def test_loginlock_intent_saved_before_disable(fake):
    fake.fail_set.add("lisa")
    lock = WinLoginLock(Accounts(fake), fake)
    lock.update({"lisa"})            # Deaktivieren scheitert – Absicht ist gespeichert
    assert store.load("winlock.json") == {"disabled": ["S-1-5-21-1-2-3-1002"]}
    fake.fail_set.clear()
    lock.update(set())               # wird wieder sauber ausgetragen
    assert store.load("winlock.json") == {"disabled": []} and not disabled(fake, "lisa")


# ---------- Sitzungen ----------

def test_sessions(fake):
    fake.sessions = [(1, api.WTSActive, "lisa", 1, 111), (2, 4, "papa", 1, 222), (3, api.WTSActive, "", -1, 0),
                     (4, api.WTSActive, "fremd", 1, 0), (5, api.WTSActive, "lisa", 0, 333)]
    s = {x.id: x for x in WinSessions(Accounts(fake), fake).list()}
    assert set(s) == {"1", "2", "5"}           # Sitzung 0, Anmeldebildschirm, Fremdkonto fehlen
    assert s["1"].counts and s["1"].graphical and s["1"].user == "lisa" and s["1"].uid == 1002
    assert not s["2"].counts and s["2"].is_user_session        # getrennt (Benutzerwechsel)
    assert not s["5"].counts                                   # gesperrt
    asyncio.run(WinSessions(Accounts(fake), fake).terminate("5"))
    assert fake.logoffs == [5]


# ---------- Benachrichtigungen ----------

class FakeTray:
    def __init__(self, connected: set[str]) -> None:
        self.connected = connected
        self.sent: list[tuple[str, object]] = []

    async def send_to_user(self, user, msg):
        if user in self.connected:
            self.sent.append((user, msg))
            return 1
        return 0


def test_notify_via_tray_and_action(fake):
    async def main():
        clicked = []

        async def on_action(user):
            clicked.append(user)

        d = WinDesktop(fake, clock=lambda: 100.0)
        d.attach(SimpleNamespace(tray=FakeTray({"lisa"}), _sessions=[]))
        await d.notify("lisa", 1002, "Bildschirmzeit", "Noch 5 Minuten", on_action=on_action)
        (_user, msg), = d.agent.tray.sent
        assert isinstance(msg, t.TrayNotify) and msg.action_id and msg.action_label == "Verlängerung anfordern"
        await d.action("papa", msg.action_id)   # anderer User: ignoriert
        await d.action("lisa", "unbekannt")
        await d.action("lisa", msg.action_id)
        assert clicked == ["lisa"]
    asyncio.run(main())


def test_critical_notify_without_tray_uses_message_box(fake):
    async def main():
        d = WinDesktop(fake)
        sess = [SimpleNamespace(id="3", user="lisa")]
        d.attach(SimpleNamespace(tray=FakeTray(set()), _sessions=sess))
        await d.notify("lisa", 1002, "Info", "unkritisch")
        await d.notify("lisa", 1002, "Bildschirmzeit zu Ende", "Abmeldung in 60 Sekunden", critical=True)
        await asyncio.sleep(0.05)
        assert fake.messages == [(3, "Bildschirmzeit zu Ende")]
        assert await d.logout("lisa", 1002) and fake.logoffs == [3]
    asyncio.run(main())


# ---------- Update ----------

SECRET = bytes(range(32))


def _manifest(version="9.0.0", data=b"installer"):
    body = json.dumps({"version": version, "file": f"KidCTL-Setup-{version}.exe",
                       "sha256": hashlib.sha256(data).hexdigest()}).encode()
    return body, base64.b64encode(ed25519.sign(SECRET, body))


def test_manifest_signature():
    key = ed25519.public_key(SECRET)
    body, sig = _manifest()
    assert upd.parse_manifest(body, sig, key)["version"] == "9.0.0"
    with pytest.raises(ValueError):
        upd.parse_manifest(body.replace(b"9.0.0", b"9.0.1"), sig, key)
    bad, bad_sig = json.dumps({"version": "1", "file": "..\\evil.exe", "sha256": "0" * 64}).encode(), None
    bad_sig = base64.b64encode(ed25519.sign(SECRET, bad))
    with pytest.raises(ValueError):
        upd.parse_manifest(bad, bad_sig, key)      # Dateiname außerhalb des Musters


def test_updater_downloads_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(upd, "UPDATE_DIR", tmp_path / "updates")
    monkeypatch.setattr(upd, "public_key", lambda: ed25519.public_key(SECRET))
    body, sig = _manifest(data=b"setup-bytes")
    files = {"https://kids.example/repo/windows/latest.json": body,
             "https://kids.example/repo/windows/latest.json.sig": sig,
             "https://kids.example/repo/windows/KidCTL-Setup-9.0.0.exe": b"setup-bytes"}

    class Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self.data[:n]

    launched = []
    now = [0.0]
    u = upd.Updater({"server_url": "https://kids.example"}, clock=lambda: now[0],
                    opener=lambda url, timeout: Resp(files[url]))
    u.launch = launched.append
    assert not u.due()                   # erst kurz nach dem Start
    now[0] = upd.FIRST_CHECK
    assert u.due() and u.check() == "9.0.0" and not u.due()
    assert launched == [tmp_path / "updates" / "KidCTL-Setup-9.0.0.exe"]
    assert u.check() is None    # dieselbe Version nicht sofort erneut
    files["https://kids.example/repo/windows/KidCTL-Setup-9.0.0.exe"] = b"manipuliert"
    store.save(upd.STATE_FILE, {})
    assert u.check() is None and len(launched) == 1


def test_updater_requires_https():
    assert upd.Updater({"server_url": "http://kids.example"}).base == ""
    assert upd.Updater({"server_url": "http://kids.example", "insecure_http": True}).base == "http://kids.example"


# ---------- WinAgent ----------

def _policy(locked=False):
    return p.Policy(version="v1", timezone="Europe/Berlin", users={"lisa": 1, "papa": 1}, children=[{
        "id": 1, "name": "Lisa", "locked": locked,
        "rules": [{"weekday": d, "quota_minutes": 120, "window_start": "00:00", "window_end": "23:59"} for d in range(7)],
    }])


@pytest.fixture
def winagent(fake, monkeypatch):
    from kidctl_agent.win import agent as wa

    monkeypatch.setattr(wa.Updater, "__init__", lambda self, device: setattr(self, "key", None) or None)
    clock = [1000.0]
    monkeypatch.setattr(wa, "boottime", lambda: clock[0])
    acc = Accounts(fake)
    desk = WinDesktop(fake, clock=lambda: clock[0])
    agent = wa.WinAgent(WinSessions(acc, fake), desk, None, acc)
    agent.langs.read = lambda sid: "de-DE"
    agent.tray = SimpleNamespace(send_to_user=FakeTray(set()).send_to_user, push_states=_noop_push,
                                 tray_icons=lambda: {})
    agent.enforcer.clock = lambda: clock[0]
    return agent, clock


async def _noop_push(now):
    return None


def test_winagent_lock_and_blocked_login(winagent, fake, monkeypatch):
    agent, clock = winagent

    async def main():
        agent.policy = _policy(locked=True)
        await agent.tick_once()
        assert disabled(fake, "lisa") and not disabled(fake, "papa")   # papa ist Admin
        assert "papa" not in agent.decisions(agent.now_local())
        # Sitzung trotz Sperre (z. B. zwischengespeicherte Anmeldung): Hinweis, Abmeldung nach 10 s
        fake.sessions = [(2, api.WTSActive, "lisa", 1, 555)]
        clock[0] += 5
        await agent.tick_once()
        await asyncio.sleep(0.05)
        assert fake.messages == [(2, "Anmeldung gesperrt")] and fake.logoffs == []
        clock[0] += 11
        await agent.tick_once()
        assert fake.logoffs == [2]
        # Freigabe: Konto wieder aktiv
        agent.policy = _policy(locked=False)
        clock[0] += 5
        await agent.tick_once()
        assert not disabled(fake, "lisa")
    asyncio.run(main())


def test_winagent_tray_state_countdown(winagent, fake):
    agent, clock = winagent

    async def main():
        fake.sessions = [(2, api.WTSActive, "lisa", 1, 555)]
        agent.policy = _policy()
        await agent.tick_once()
        assert agent.tray_state("lisa").logout_in_seconds is None
        agent.policy = _policy(locked=True)    # „Jetzt sperren“ mit Standard-Vorwarnung
        clock[0] += 5
        await agent.tick_once()
        state = agent.tray_state("lisa")
        assert not state.allowed and 0 < state.logout_in_seconds <= 60
    asyncio.run(main())


def test_winagent_integrity_admin_child(winagent, fake, monkeypatch):
    agent, _clock = winagent
    from kidctl_agent.win import integrity

    agent.policy = _policy()
    monkeypatch.setattr(integrity, "_read", lambda key, value="": "Service" if "SafeBoot" in key else "x")
    monkeypatch.setattr(integrity.check_safeboot, "__defaults__", (integrity._read,))
    monkeypatch.setattr(integrity.check_tray_autostart, "__defaults__", (integrity._read,))
    checks = {c.id: c for c in agent._run_checks()}
    assert checks["safeboot"].ok and checks["tray-autostart"].ok
    assert not checks["admin:papa"].ok and "admin:lisa" not in checks


def test_winagent_restarts_missing_tray(winagent, fake):
    agent, clock = winagent
    started = []
    agent.tray_command = ('"C:\\KidCTL\\python\\pythonw.exe" -m kidctl_tray', "C:\\KidCTL\\python")
    agent.run_in_session = lambda sid, cmd, cwd: started.append(sid) or 4711
    agent.tray.clients = set()

    async def main():
        fake.sessions = [(2, api.WTSActive, "lisa", 1, 555), (3, 4, "kind2", 1, 556), (4, api.WTSActive, "papa", 1, 557)]
        agent.policy = _policy()
        await agent.tick_once()
        assert started == []                    # erst Zeit für den Autostart lassen
        clock[0] += 61
        await agent.tick_once()
        assert started == [2]                   # nur aktive Kinder-Sitzung (papa ist Admin)
        clock[0] += 60
        await agent.tick_once()
        assert started == [2]                   # nicht ständig neu
        agent.tray.clients = [SimpleNamespace(user="lisa")]
        clock[0] += 300
        await agent.tick_once()
        assert started == [2]                   # verbunden -> nichts zu tun
        agent.tray.clients = set()              # Abmeldung angefordert: Tray-App beendet, Sitzung noch aktiv
        agent.enforcer.hard["lisa"] = clock[0] + 1000

        async def keep(*a):                     # Enforcer hier nicht abräumen lassen (Lisa ist im Test erlaubt)
            pass
        agent.enforcer.step = keep
        clock[0] += 300
        await agent.tick_once()
        assert started == [2]
    asyncio.run(main())


def test_safe_mode_guard_imports_without_asyncio():
    """Im minimalen abgesicherten Modus ist asyncio nicht ladbar (WinError 10050)."""
    import subprocess
    import sys
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('asyncio', '_overlapped', 'websockets'):\n"
        "            raise ImportError(name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "for m in list(sys.modules):\n"
        "    if m.split('.')[0] == 'asyncio':\n"
        "        del sys.modules[m]\n"
        "import kidctl_agent.win.safemode\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**__import__('os').environ, "PYTHONPATH": ":".join(sys.path)})
    assert r.returncode == 0, r.stderr


def test_safe_mode_guard_locks_children_only(fake):
    from kidctl_agent.win import safemode
    store.save(store.POLICY_FILE, {"users": {"lisa": 1, "papa": 2}})      # papa ist Admin: nie sperren
    fake.sessions = [(1, api.WTSActive, "", -1, 0), (2, api.WTSActive, "lisa", 1, 5),
                     (3, 4, "papa", 1, 6), (4, api.WTSActive, "mama", 1, 7)]
    guard = safemode.Guard(backend=fake)
    guard.langs.lang_of = lambda user: "de"
    guard.tick()
    assert guard.lock.ours and fake.user_flags("lisa") & api.UF_ACCOUNTDISABLE
    assert not fake.user_flags("papa") & api.UF_ACCOUNTDISABLE
    assert fake.logoffs == [2]                                            # nur das Kind
    assert [m[0] for m in fake.messages] == [2]
    assert fake.killed == [("LogonUI.exe", 1)]                            # Kontenliste aktualisieren
    fake.sessions = [(1, api.WTSActive, "", -1, 0)]
    guard.tick()
    assert len(fake.killed) == 1                                          # keine Änderung -> nichts


def test_safe_mode_guard_without_policy_locks_nobody(fake):
    from kidctl_agent.win import safemode
    fake.sessions = [(2, api.WTSActive, "lisa", 1, 5)]
    guard = safemode.Guard(backend=fake)
    guard.tick()
    assert not guard.lock.ours and fake.logoffs == []


def test_windows_modules_import_without_posix_modules():
    """Unter Windows fehlen pwd/grp/fcntl/dbus_next: Der Windows-Agent darf sie nicht (indirekt) importieren."""
    import subprocess
    import sys
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('pwd', 'grp', 'fcntl', 'dbus_next', 'termios'):\n"
        "            raise ImportError(name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "for m in list(sys.modules):\n"
        "    if m.split('.')[0] in ('pwd', 'grp', 'fcntl', 'termios'):\n"
        "        del sys.modules[m]\n"
        "import kidctl_agent.cli, kidctl_agent.win.agent, kidctl_agent.win.service, kidctl_agent.win.update\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**__import__('os').environ, "PYTHONPATH": ":".join(sys.path)})
    assert r.returncode == 0, r.stderr


def test_winagent_refreshes_login_screen_on_lock_change(winagent, fake):
    agent, clock = winagent

    async def main():
        # Anmeldebildschirm, getrennter Papa, vorne gesperrte Sitzung von Mama („Benutzer wechseln“), entsperrter Tim
        fake.sessions = [(1, api.WTSActive, "", -1, 0), (2, 4, "papa", 0, 9), (3, api.WTSActive, "mama", 0, 10),
                         (4, api.WTSActive, "tim", 1, 11)]
        agent.policy = _policy(locked=True)
        await agent.tick_once()
        assert fake.killed == [("LogonUI.exe", 1), ("LogonUI.exe", 3)]
        clock[0] += 5
        await agent.tick_once()
        assert len(fake.killed) == 2                      # keine Änderung -> nichts
        agent.policy = _policy()
        clock[0] += 5
        await agent.tick_once()
        assert len(fake.killed) == 4                      # wieder aktiviert -> erneut
    asyncio.run(main())
