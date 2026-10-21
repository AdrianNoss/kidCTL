"""Sprache der Meldungen beim Kind: Katalog vollständig, Sitzungssprache, /etc/locale.conf, englische Texte,
sprachneutrale Begründungen und englische Programmnamen."""

import asyncio
import getpass
import pwd
from pathlib import Path

from kidctl_common import i18n
from kidctl_common.i18n_check import extract_python, problems, unused

from kidctl_agent import apps, dnsfilter
from kidctl_agent.lang import UserLanguages, parse_locale_conf, system_language
from kidctl_agent.procs import ProcInfo
from kidctl_agent.programs import Allowed, classify

PKG = Path(__file__).resolve().parents[1] / "kidctl_agent"
PO = PKG / "locale" / "en.po"
ME = getpass.getuser()


def _msgids():
    return extract_python(list(PKG.rglob("*.py")))


def test_every_text_has_english_translation():
    assert problems(PO, _msgids()) == []


def test_no_stale_translations():
    assert unused(PO, _msgids()) == []


def test_system_language(tmp_path):
    f = tmp_path / "locale.conf"
    f.write_text('LANG="de_DE.UTF-8"\n# Kommentar\n')
    assert system_language(f) == "de"
    f.write_text("LANG=en_US.UTF-8\nLC_MESSAGES=de_AT.UTF-8\n")
    assert system_language(f) == "de"                       # LC_MESSAGES hat Vorrang
    assert system_language(tmp_path / "fehlt") == "en"
    assert parse_locale_conf("LANG='fr_FR.UTF-8'") == {"LANG": "fr_FR.UTF-8"}


def _proc(pid, exe):
    return ProcInfo(pid=pid, uid=pwd.getpwnam(ME).pw_uid, ppid=1, start=1, exe=exe, argv=(exe,), tty=False,
                    flatpak_id=None)


def test_session_language_and_cache():
    procs = {9: _proc(9, "/usr/bin/startplasma-wayland"), 10: _proc(10, "/usr/lib/systemd/systemd"),
             11: _proc(11, "/usr/bin/plasmashell"), 12: _proc(12, "/usr/bin/konsole")}
    # startplasma hat noch die Systemsprache in der Startumgebung (setzt LANGUAGE erst danach)
    envs = {9: {"LANG": "de_DE.UTF-8"}, 10: {"LANG": "de_DE.UTF-8"},
            11: {"LANG": "de_DE.UTF-8", "LANGUAGE": "en_US"}, 12: {"LANG": "de_DE.UTF-8"}}
    clock = [0.0]
    langs = UserLanguages(list_pids=lambda uids: list(procs), read=procs.get, environ=lambda pid: envs[pid],
                          clock=lambda: clock[0], system=lambda: "de", accounts=lambda user: None)
    assert langs.lang_of(ME) == "en"                         # Plasma-Sitzung, nicht systemd/konsole
    envs[11] = {"LANG": "de_DE.UTF-8"}
    assert langs.lang_of(ME) == "en"                         # zwischengespeichert
    clock[0] = 61
    assert langs.lang_of(ME) == "de"
    assert langs.lang_of("gibt-es-nicht") == "de"            # ohne Sitzung: /etc/locale.conf
    del procs[11]
    clock[0] = 200
    assert langs.lang_of(ME) == "de"                         # anderer Prozess (konsole) als Rückfall
    # Tray-App (Python-Skript) hat Vorrang: zeigt dem Kind dieselbe Sprache
    procs[13] = ProcInfo(pid=13, uid=procs[12].uid, ppid=1, start=1, exe="/usr/bin/python3.14",
                         argv=("/usr/bin/python3", "-E", "-s", "/usr/lib/kidctl/kidctl-tray-main.py"), tty=False,
                         flatpak_id=None)
    envs[13] = {"LANG": "de_DE.UTF-8", "LANGUAGE": "en_GB:en"}
    clock[0] = 300
    assert langs.lang_of(ME) == "en"


def test_gnome_session_and_accounts_language(tmp_path):
    from kidctl_agent.lang import accounts_language
    (tmp_path / "lea").write_text("[User]\nLanguage=en_GB.UTF-8\nSession=gnome\n")
    (tmp_path / "tom").write_text("[User]\nSession=gnome\n")
    (tmp_path / "max").write_text("[User]\nLanguages=de_DE.UTF-8;en_US.UTF-8;\n")   # neueres Format
    assert accounts_language("lea", tmp_path) == "en"
    assert accounts_language("max", tmp_path) == "de"
    assert accounts_language("tom", tmp_path) is None        # keine Sprache gewählt
    assert accounts_language("fehlt", tmp_path) is None

    procs = {20: _proc(20, "/usr/lib/gdm-wayland-session"), 21: _proc(21, "/usr/bin/gnome-shell"),
             22: _proc(22, "/usr/bin/nautilus")}
    envs = {20: {"LANG": "de_DE.UTF-8"}, 21: {"LANG": "en_US.UTF-8"}, 22: {"LANG": "de_DE.UTF-8"}}
    accounts = {ME: "fr"}
    langs = UserLanguages(list_pids=lambda uids: list(procs), read=procs.get, environ=lambda pid: envs[pid],
                          clock=iter(range(0, 10**6, 100)).__next__, system=lambda: "de",
                          accounts=lambda user: accounts.get(user))
    assert langs.lang_of(ME) == "en"                         # gnome-shell
    del procs[21]
    assert langs.lang_of(ME) == "fr"                         # ohne Shell: Kontoeinstellung vor anderen Prozessen
    accounts.clear()
    assert langs.lang_of(ME) == "de"                         # anderer Prozess (gdm-wayland-session ignoriert)


def test_notifications_in_child_language():
    from kidctl_agent.gamelimit import GameLimit
    from datetime import date
    sent = []

    class Desk:
        async def notify(self, user, uid, title, body, critical=False, on_action=None, action=None):
            sent.append((title, body, action))
    g = GameLimit(Desk(), kill=lambda *a: None, clock=lambda: 0.0, lang_of=lambda u: "en")
    asyncio.run(g.step("lea", 1001, date(2026, 10, 20), 120, True, []))
    asyncio.run(g.step("lea", 1001, date(2026, 10, 20), 0, True, [(5, 1)]))
    assert sent == [("Game time", "2 minutes of game time left – games will be closed afterwards. Please save.",
                     "Ask for more game time"),
                    ("Game time used up", "Games are closed for today. You can ask for more game time.",
                     "Ask for more game time")]
    assert i18n.current() == "de"                            # Sprache gilt nur für die Meldung


def test_detail_codes():
    a = Allowed(["/usr/bin/kwin_wayland"], set())
    sh = ProcInfo(pid=5, uid=1001, ppid=1, start=1, exe="/usr/bin/bash", argv=("bash", "/home/k/x.sh"), tty=False,
                  flatpak_id=None)
    v = classify(sh, a)
    assert (v.code, v.arg, v.detail) == ("script", "bash", "Skript (bash)")
    assert dnsfilter.source_code("Sperrliste HaGeZi NSFW") == ("blocklist", "HaGeZi NSFW")
    assert dnsfilter.source_code("nicht freigegeben") == ("not_allowed", "")
    assert dnsfilter.source_code("???") == ("", "")


def test_english_app_name(tmp_path):
    f = tmp_path / "x.desktop"
    f.write_text("[Desktop Entry]\nType=Application\nName=System Monitor\nName[de]=Systemmonitor\nExec=true\n")
    e = apps.parse_desktop(f)
    assert (e.name, e.name_en) == ("Systemmonitor", "System Monitor")
