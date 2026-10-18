"""Auslesen der .desktop-Einträge für den Programmkatalog."""

from kidctl_agent.apps import parse_desktop


def test_categories_read(tmp_path):
    f = tmp_path / "supertux.desktop"
    f.write_text("[Desktop Entry]\nType=Application\nName=SuperTux\nExec=/usr/bin/env true\n"
                 "Categories=Game;ArcadeGame; ;\n")
    app = parse_desktop(f)
    assert app is not None and app.categories == ["Game", "ArcadeGame"]


def test_no_categories(tmp_path):
    f = tmp_path / "x.desktop"
    f.write_text("[Desktop Entry]\nType=Application\nName=X\nExec=/usr/bin/env true\n")
    assert parse_desktop(f).categories == []


def test_shells_from_etc_shells(tmp_path, monkeypatch):
    from kidctl_agent import apps
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("bash", "fish", "git-shell"):
        (bin_ / name).write_text("")
    (bin_ / "sh").symlink_to(bin_ / "bash")
    (bin_ / "rbash").symlink_to(bin_ / "bash")
    etc = tmp_path / "shells"
    etc.write_text("# Kommentar\n" + "\n".join(str(bin_ / n) for n in ("sh", "bash", "rbash", "fish", "git-shell", "zsh")) + "\n")
    shells = apps.scan_shells(str(etc))
    assert sorted((s.key, s.name, s.categories) for s in shells) == [
        (f"exe:{bin_}/bash", "bash (Shell)", ["Shell"]),
        (f"exe:{bin_}/fish", "fish (Shell)", ["Shell"])]          # sh/rbash -> bash, git-shell/zsh nicht
    assert apps.scan_shells(str(tmp_path / "fehlt")) == []


def test_scan_apps_includes_shells_without_duplicates(tmp_path):
    from kidctl_agent import apps
    d = tmp_path / "apps"
    d.mkdir()
    (d / "x.desktop").write_text("[Desktop Entry]\nType=Application\nName=X\nExec=/usr/bin/env true\n")
    etc = tmp_path / "shells"
    etc.write_text("/usr/bin/env\n")                     # kein Shell-Name -> ignoriert
    assert [a.name for a in apps.scan_apps([d], str(etc))] == ["X"]
