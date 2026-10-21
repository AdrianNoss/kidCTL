"""GNOME-Suchanbieter nicht freigegebener Apps abschalten (gnomesearch)."""

import subprocess

from kidctl_agent import gnomesearch, store
from kidctl_agent.gnomesearch import GnomeSearch, format_strv, parse_strv


def test_providers_and_strv(tmp_path):
    prov, apps = tmp_path / "prov", tmp_path / "apps"
    prov.mkdir()
    apps.mkdir()
    (prov / "files.ini").write_text("[Shell Search Provider]\nDesktopId=org.gnome.Nautilus.desktop\nBusName=x\n")
    (prov / "kaputt.ini").write_text("kein ini")
    (prov / "fehlt.ini").write_text("[Shell Search Provider]\nDesktopId=gibt.es.nicht.desktop\n")
    (apps / "org.gnome.Nautilus.desktop").write_text("[Desktop Entry]\nType=Application\nName=Files\nExec=/usr/bin/true\n")
    assert gnomesearch.providers(prov, [str(apps)]) == {"org.gnome.Nautilus.desktop": ("exe", "/usr/bin/true")}
    assert parse_strv("@as []\n") == [] and parse_strv("['a.desktop', 'b.desktop']") == ["a.desktop", "b.desktop"]
    assert parse_strv("unsinn(") == []
    assert format_strv(set()) == "@as []" and format_strv({"b", "a"}) == "['a', 'b']"


def test_sync_keeps_childs_own_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    value = ["eigener.desktop"]   # vom Kind selbst abgeschaltet
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[1:])
        if cmd[1] == "get":
            return subprocess.CompletedProcess(cmd, 0, format_strv(set(value)) + "\n", "")
        value[:] = parse_strv(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "", "")
    s = GnomeSearch(lambda user, uid, *cmd: list(cmd), run=run)
    s.sync("lea", 1001, {"org.gnome.Nautilus.desktop", "org.gnome.clocks.desktop"})
    assert sorted(value) == ["eigener.desktop", "org.gnome.Nautilus.desktop", "org.gnome.clocks.desktop"]
    calls.clear()
    s.sync("lea", 1001, {"org.gnome.Nautilus.desktop", "org.gnome.clocks.desktop"})
    assert calls == []                                   # unverändert: kein gsettings-Aufruf
    s.sync("lea", 1001, {"org.gnome.clocks.desktop"})     # Dateien freigegeben
    assert sorted(value) == ["eigener.desktop", "org.gnome.clocks.desktop"]
    assert GnomeSearch(lambda *a: None).managed == {"lea": ["org.gnome.clocks.desktop"]}   # gespeichert
    s.sync("lea", 1001, set())                           # Programmkontrolle aus
    assert value == ["eigener.desktop"] and s.managed == {}


def test_sync_without_session_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    s = GnomeSearch(lambda *a: None)                     # kein Session-Bus
    s.sync("lea", 1001, {"x.desktop"})
    assert s.managed == {}                               # nichts gemerkt -> nächster Abgleich versucht es erneut
