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
