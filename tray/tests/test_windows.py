"""Windows-Teile der Tray-App, soweit ohne Windows testbar (Countdown-Logik, Toast-XML, kidctl://-Adressen)."""

from __future__ import annotations

import xml.dom.minidom

import pytest

from kidctl_common import tray as t

from kidctl_tray.countdown import CountdownTracker, CountdownWindow, texts_for
from kidctl_tray.winplatform import parse_url, toast_xml


@pytest.fixture(scope="module")
def qapp():
    from kidctl_tray.qt import QApplication
    return QApplication.instance() or QApplication([])


def state(**kw) -> t.TrayState:
    base = dict(restricted=True, child_name="Lisa", allowed=True, remaining_seconds=3600)
    base.update(kw)
    return t.TrayState(**base)


def test_countdown_tracker_phases():
    now = [0.0]
    tr = CountdownTracker(clock=lambda: now[0])
    tr.update(state(remaining_seconds=600))
    assert tr.due() is None
    tr.update(state(remaining_seconds=50))                   # letzte Minute
    assert tr.due().kind == "warn" and tr.due().seconds == 50
    tr.dismiss("warn")
    now[0] = 10
    tr.update(state(remaining_seconds=40))
    assert tr.due() is None                                  # in derselben Phase nicht erneut
    tr.update(state(allowed=False, remaining_seconds=0, reason="quota", logout_in_seconds=60))
    d = tr.due()
    assert d.kind == "logout" and d.seconds == 60 and d.can_request
    now[0] = 40
    assert tr.due().seconds == 30                            # zählt selbst herunter
    tr.update(state(remaining_seconds=1800))                 # verlängert
    assert tr.due() is None
    tr.update(state(remaining_seconds=50))                   # neue Phase: wieder sichtbar
    assert tr.due().kind == "warn"


def test_countdown_long_lock_shows_last_minute_only():
    now = [0.0]
    tr = CountdownTracker(clock=lambda: now[0])
    tr.update(state(allowed=False, remaining_seconds=0, reason="locked", logout_in_seconds=1800))
    assert tr.due() is None
    now[0] = 1750
    d = tr.due()
    assert d.kind == "logout" and d.seconds == 50 and not d.can_request
    assert texts_for(d)[0] == "Gesperrt"


def test_countdown_window(qapp):
    w = CountdownWindow(CountdownTracker(clock=lambda: 0.0))
    w.set_state(state(allowed=False, remaining_seconds=0, reason="quota", logout_in_seconds=45))
    assert w.isVisible() and "45" in w.text.text() and w.request.isVisible()
    w.close()
    w.set_state(state(allowed=False, remaining_seconds=0, reason="quota", logout_in_seconds=40))
    assert not w.isVisible()
    w.set_state(state())
    assert not w.isVisible()


def test_toast_xml_escapes_and_actions():
    x = toast_xml("Nachricht von <Mama>", 'Komm & "iss"', "ab12", "Verlängerung anfordern", True)
    doc = xml.dom.minidom.parseString(x)
    texts = [n.firstChild.data for n in doc.getElementsByTagName("text")]
    assert texts == ["Nachricht von <Mama>", 'Komm & "iss"']
    action, = doc.getElementsByTagName("action")
    assert action.getAttribute("arguments") == "kidctl://action/ab12"
    assert doc.documentElement.getAttribute("scenario") == "reminder"
    plain = xml.dom.minidom.parseString(toast_xml("a", "b", None, "", False))
    assert not plain.getElementsByTagName("action") and not plain.documentElement.getAttribute("scenario")


def test_parse_url():
    assert parse_url("kidctl://action/ab12") == ("action", "ab12")
    assert parse_url("kidctl://action/ab12/") == ("action", "ab12")
    assert parse_url("kidctl://") == ("show", "")
    assert parse_url("kidctl://action/../x") is None
    assert parse_url("kidctl://action/AB;calc") is None
    assert parse_url("--show") is None
