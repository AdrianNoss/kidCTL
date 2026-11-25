"""„Zählt nicht“-Programme (Server): Markierung im Katalog, Sperrliste nicht ausnehmbarer Programme, Policy."""

import html

from sqlalchemy import select

from kidctl_common import protocol as p
from kidctl_common.freeapps import never_free

from conftest import SessionLocal
from app.models import ChildAppRule, Device

from test_policy import _policy, _setup, device
from test_programs import _sync

APPS = [
    p.AppEntry(key="exe:/usr/bin/gcompris-qt", name="GCompris", categories=["Education"]),
    p.AppEntry(key="exe:/usr/lib/libreoffice/program/soffice", name="LibreOffice Writer", categories=["Office"]),
    p.AppEntry(key="exe:/usr/lib/firefox/firefox", name="Firefox", categories=["Network", "WebBrowser"]),
    p.AppEntry(key="exe:/usr/bin/lernbrowser", name="Lernbrowser", categories=["Education", "WebBrowser"]),
    p.AppEntry(key="exe:/usr/bin/konsole", name="Konsole", categories=["System", "TerminalEmulator"]),
    p.AppEntry(key="exe:/usr/bin/kate", name="Kate", categories=["Utility", "Development"]),
    p.AppEntry(key="exe:/usr/bin/supertux2", name="SuperTux", categories=["Game"]),
    p.AppEntry(key="exe:/usr/bin/steam", name="Steam", categories=["Game"]),
    p.AppEntry(key="exe:/usr/bin/chromium", name="Chromium"),    # Name genügt, auch ohne Kategorie
    p.AppEntry(key="exe:C:\\Program Files\\Mozilla Firefox\\firefox.exe", name="Firefox (Windows)"),
    p.AppEntry(key="exe:/usr/bin/tuxmath", name="TuxMath", categories=["Education"]),
]


def _save(client, cid, tok, free, games=(), apps=()):
    return client.post(f"/children/{cid}/programs", data={
        "csrf_token": tok, "program_mode": "enforce", "app": list(apps), "game": list(games), "free": list(free)},
        follow_redirects=False)


def test_free_apps_marked_and_in_policy(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        assert _policy(ws).children[0].free_apps == []
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        everything = [a.key for a in APPS] + ["flatpak:org.mozilla.firefox", "javascript:boese"]
        r = _save(client, cid, tok, free=everything, games=["exe:/usr/bin/tuxmath"], apps=[a.key for a in APPS])
        assert r.status_code == 303
        # nur Lern-App und Office; Browser (auch per Kategorie), Terminal, Entwicklung, Spiele, Launcher abgelehnt
        assert _policy(ws).children[0].free_apps == ["exe:/usr/bin/gcompris-qt",
                                                     "exe:/usr/lib/libreoffice/program/soffice"]

        page = client.get(f"/children/{cid}/programme").text
        assert page.count('name="free"') == 2 + 1    # GCompris, LibreOffice + TuxMath (Spiel, per CSS ausgeblendet)
        assert 'name="free" value="exe:/usr/bin/gcompris-qt" checked' in page
        assert 'name="free" value="exe:/usr/lib/firefox/firefox"' not in page
        assert 'name="free" value="exe:/usr/bin/konsole"' not in page
        # Gerät ohne Fähigkeit „free_apps“ -> Hinweis
        assert "kennt „Zählt nicht“ noch nicht" in page

        with SessionLocal() as db:
            dev = db.get(Device, dev_id)
            dev.features = p.FEATURE_FREE_APPS
            db.commit()
        assert "kennt „Zählt nicht“ noch nicht" not in client.get(f"/children/{cid}/programme").text

        # Abwahl entfernt die Regel wieder
        _save(client, cid, tok, free=["exe:/usr/bin/gcompris-qt"])
        assert _policy(ws).children[0].free_apps == ["exe:/usr/bin/gcompris-qt"]
        _save(client, cid, tok, free=[])
        assert _policy(ws).children[0].free_apps == []
        with SessionLocal() as db:
            assert db.scalars(select(ChildAppRule)).all() == []


def test_free_and_game_exclusive(client, parent):
    """Ein als Spiel markiertes Programm kann nicht gleichzeitig „zählt nicht“ sein."""
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _dev):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        _save(client, cid, tok, free=["exe:/usr/bin/gcompris-qt"], games=["exe:/usr/bin/gcompris-qt"])
        pol = _policy(ws).children[0]
        assert pol.game_apps == ["exe:/usr/bin/gcompris-qt"] and pol.free_apps == []


def test_english_page(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _dev):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        _save(client, cid, tok, free=["exe:/usr/bin/gcompris-qt"])
        client.get(f"/lang/en?next=/children/{cid}/programme")
        page = html.unescape(client.get(f"/children/{cid}/programme").text)
        assert "Doesn't count" in page and "What does “Doesn't count” mean?" in page


def test_never_free_list():
    assert never_free("exe:/usr/lib/firefox/firefox")
    assert never_free("exe:C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
    assert never_free("exe:C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe")
    assert never_free("exe:/usr/bin/python3.13") and never_free("exe:/usr/bin/bash")
    assert never_free("flatpak:org.mozilla.firefox") and never_free("flatpak:com.valvesoftware.Steam")
    assert never_free("snap:firefox") and never_free("appx:Microsoft.WindowsTerminal_8wekyb3d8bbwe")
    assert never_free("exe:/usr/bin/steam") and never_free("snap:steam")
    assert never_free("game:steam:620")
    assert never_free("exe:/usr/bin/lernapp", ["Education", "WebBrowser"])
    assert not never_free("exe:/usr/bin/gcompris-qt", ["Education"])
    assert not never_free("flatpak:org.kde.gcompris") and not never_free("appx:Microsoft.Office.OneNote_8wekyb3d8bbwe")
