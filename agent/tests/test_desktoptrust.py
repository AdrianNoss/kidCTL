"""Desktop-Verknüpfungen erlaubter Programme vertrauen (KDE startet sie bei noexec-Home sonst nicht)."""

import os

import pytest

from kidctl_agent import desktoptrust as dt
from kidctl_agent.programs import Allowed

UID = os.getuid()


def entry(exec_line, extra=""):
    return f"[Desktop Entry]\nType=Application\nName=X\nExec={exec_line}\n{extra}"


@pytest.fixture
def chowns(monkeypatch):
    calls = []
    monkeypatch.setattr(dt.os, "fchown", lambda fd, u, g: calls.append(os.readlink(f"/proc/self/fd/{fd}")))
    monkeypatch.setattr(dt.os, "fchmod", lambda fd, m: None)
    return calls


def allow(*keys, base=frozenset()):
    a = Allowed([], set(keys), frozenset(base), [])
    return lambda t: dt.target_allowed(t, a)


def test_entry_target():
    assert dt.entry_target(entry("/usr/bin/env true")) == ("exe", os.path.realpath("/usr/bin/true"))
    assert dt.entry_target(entry("x", "X-Flatpak=com.heroicgameslauncher.hgl\n")) == (
        "flatpak", "com.heroicgameslauncher.hgl")
    assert dt.entry_target("[Desktop Entry]\nType=Link\nURL=https://x\n") is None
    assert dt.entry_target("kaputt") is None


def test_trusts_only_allowed_entries(tmp_path, chowns):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    true = os.path.realpath("/usr/bin/true")
    (home / "Desktop/spiel.desktop").write_text(entry(f"{true} steam://rungameid/1"))
    (home / "Desktop/fremd.desktop").write_text(entry("/usr/bin/env false"))
    (home / "Desktop/notiz.txt").write_text(entry(true))
    trusted = dt.trust_desktop(str(home), UID, allow(f"exe:{true}"))
    assert trusted == [str(home / "Desktop/spiel.desktop")]
    assert chowns == [str(home / "Desktop/spiel.desktop")]


def test_xdg_desktop_dir_and_flatpak(tmp_path, chowns):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config/user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n')
    (home / "Schreibtisch").mkdir()
    (home / "Schreibtisch/heroic.desktop").write_text(entry("flatpak run x", "X-Flatpak=com.heroic\n"))
    assert dt.trust_desktop(str(home), UID, allow("flatpak:com.heroic")) == [str(home / "Schreibtisch/heroic.desktop")]


def test_refuses_symlinks_hardlinks_and_foreign_paths(tmp_path, chowns):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    true = os.path.realpath("/usr/bin/true")
    target = tmp_path / "ziel.desktop"
    target.write_text(entry(true))
    (home / "Desktop/link.desktop").symlink_to(target)          # Symlink -> nie
    os.link(target, home / "Desktop/hard.desktop")              # harter Link -> nie
    (home / ".config").mkdir()
    (home / ".config/user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/../../etc"\n')  # kein Unterordner
    assert dt.trust_desktop(str(home), UID, allow(f"exe:{true}")) == []
    assert chowns == []
    # Desktop-Ordner selbst als Symlink -> nichts
    os.rename(home / "Desktop", tmp_path / "echt")
    (home / "Desktop").symlink_to(tmp_path / "echt")
    assert dt.trust_desktop(str(home), UID, allow(f"exe:{true}")) == []


def test_foreign_owner_skipped(tmp_path, chowns):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    (home / "Desktop/a.desktop").write_text(entry("/usr/bin/env true"))
    assert dt.trust_desktop(str(home), UID + 1, allow(f"exe:{os.path.realpath('/usr/bin/true')}")) == []


def test_system_shell_script_counts_as_allowed(tmp_path, monkeypatch):
    script = tmp_path / "xdg-open"
    script.write_text("#!/bin/sh\necho\n")
    monkeypatch.setattr(dt, "SYSTEM_SCRIPT_DIRS", (str(tmp_path) + "/",))
    assert dt._system_shell_script(str(script))
    script.write_text("#!/usr/bin/env python3\n")
    assert not dt._system_shell_script(str(script))
    monkeypatch.setattr(dt, "SYSTEM_SCRIPT_DIRS", ("/usr/",))
    script.write_text("#!/bin/sh\n")
    assert not dt._system_shell_script(str(script))             # nicht aus Systemverzeichnis
