#!/usr/bin/env python3
"""Screenshots der Tray-App unter Windows (Fluent-Stil) aus der Test-VM – zum Prüfen vor einem Release und für
Projektseite/Wiki.

1. **Fenster** (Standard): kopiert den Tray-Code in die VM (wie ``dev/vm/winsync.sh``, ohne Dienst-Neustart) und
   rendert dort mit dem eingebetteten Python, echter Segoe-Schrift und dem Windows-Stil jedes Fenster in
   Beispielzuständen – Countdown, Verlängerung anfragen (mit Spiele-Limit), Freigabe anfragen, Gesperrt, Chat mit
   Verlauf, Tray-Menü, Tray-Symbole – je hell/dunkel und DE/EN.
2. **Desktop** (``--desktop NAME``): Bildschirmabzug der laufenden VM über QMP, z. B. für Toasts, das Tray-Symbol in
   der Taskleiste oder die Kopplungsseite des Installers (vorher in der VM von Hand öffnen).

    dev/winshots.py [--out DIR] [--lang de,en] [--scheme light,dark]
    dev/winshots.py --desktop toast-chat

Ausgabe: ``DIR/<lang>/<light|dark>/<name>.png`` bzw. ``DIR/desktop/<name>.png`` (Standard: ``build/winshots``, nicht
im Repo). Windows 10 hat keine Test-VM – dort gilt dasselbe Stylesheet ohne Mica.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WINVM = REPO / "dev" / "vm" / "winvm.sh"
VM_DIR = "C:/Users/admin/winshots"
PYTHON = r"C:\Program Files\KidCTL\python\python.exe"

# Läuft in der VM (eingebettetes Python der Installation): rendert alle Fenster per grab() in PNG-Dateien
VM_SCRIPT = r'''
import os, sys
sys.path[:0] = [os.path.join(os.path.dirname(__file__), "lib")]
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from datetime import datetime, time, timedelta, timezone
from kidctl_common import tray as t
from kidctl_common.protocol import ChatMessage
from kidctl_tray.qt import QApplication, QColor, QMenu, QPainter, QPixmap, Qt
from kidctl_tray import texts, winstyle as ws

out, langs, schemes = sys.argv[1], sys.argv[2].split(","), sys.argv[3].split(",")
app = QApplication(sys.argv)
ws.FORCE = True
ws.apply(app)
now = datetime.now(timezone.utc)

def state(**kw):
    base = dict(restricted=True, child_name="Lisa", allowed=True, remaining_seconds=42 * 60, used_seconds=78 * 60,
                allowed_minutes=120, window_start=time(8), window_end=time(20), online=True,
                game_limit_minutes=60, game_used_seconds=35 * 60)
    base.update(kw)
    return t.TrayState(**base)

def msg(i, child, body, **kw):
    return ChatMessage(id=i, child_id=1, from_child=child, sender_name="Mama", kind=kw.pop("kind", "text"), body=body,
                       created_at=now - timedelta(minutes=30 - i * 5), **kw)

def save(widget, name, folder, w=None, h=None):
    if w:
        widget.resize(w, h or widget.sizeHint().height())
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(folder, name + ".png"))
    widget.close()

for lang in langs:
    from kidctl_common import i18n
    i18n.set_current(lang)
    from kidctl_tray import countdown, ui
    _ = texts._
    for scheme in schemes:
        dark = scheme == "dark"
        ws.STYLE.dark, ws.STYLE.accent = dark, "#0078d4"
        ws.STYLE.tokens = ws.tokens(dark, "#0078d4")
        try:   # nativer Stil „windows11“ zeichnet nach dem Schema (Qt ≥ 6.8)
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
        except AttributeError:
            pass
        ws.STYLE.set(app)
        folder = os.path.join(out, lang, scheme)
        os.makedirs(folder, exist_ok=True)
        st = state()
        cd = countdown.CountdownWindow(countdown.CountdownTracker(clock=lambda: 0.0))
        cd.set_state(state(allowed=True, remaining_seconds=45, used_seconds=7155))
        save(cd, "countdown", folder, 420)
        cd = countdown.CountdownWindow(countdown.CountdownTracker(clock=lambda: 0.0))
        cd.set_state(state(allowed=True, gaming=True, game_used_seconds=3600 - 30))
        save(cd, "countdown-games", folder, 420)
        save(ui.RequestDialog(st), "request", folder, 400)
        item = t.BlockedItem(access_kind="program", key="exe:x", name="Minecraft Launcher", last_at=now, count=2)
        save(ui.AccessDialog(item), "access", folder, 400)
        blocked = ui.BlockedWindow()
        blocked.set_items([item, t.BlockedItem(access_kind="domain", key="domain:tiktok.com", name="tiktok.com",
                                                last_at=now, count=5, pending=True)])
        save(blocked, "blocked", folder, 420, 420)
        chat = ui.ChatWindow()
        chat.set_state(st, True)
        chat.set_history([
            msg(1, True, _("Darf ich noch eine Runde spielen?"), kind="extension_request", request_target="games",
                requested_minutes=20, status="approved", approved_minutes=15),
            msg(2, False, _("Ja, aber danach Hausaufgaben.")),
            msg(3, True, "Ok 👍")])
        save(chat, "chat", folder, 440, 560)
        menu = QMenu()
        menu.addAction(texts.status_line(st)).setEnabled(False)
        menu.addSeparator()
        for label in (_("Chat öffnen"), _("Verlängerung anfordern …"), _("Gesperrt – Freigabe anfragen …")):
            menu.addAction(label)
        save(menu, "tray-menu", folder)
        # Tray-Symbole auf Taskleisten-Hintergrund (hell/dunkel) in allen Zuständen
        pix = QPixmap(4 * 48, 48)
        pix.fill(QColor("#eeeeee" if not dark else "#1c1c1c"))
        p = QPainter(pix)
        for i, level in enumerate(("ok", "warning", "critical", "offline")):
            ws.tray_icon(level, not dark, 32).paint(p, i * 48 + 8, 8, 32, 32)
        p.end()
        pix.save(os.path.join(folder, "tray-icons.png"))
print("ok")
'''


def winvm(*args: str, capture: bool = False) -> str:
    r = subprocess.run([str(WINVM), *args], check=True, text=True,
                       stdout=subprocess.PIPE if capture else None, stderr=subprocess.DEVNULL if capture else None)
    return r.stdout or ""


def windows(out: Path, langs: str, schemes: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "winshots.tar"
        with tarfile.open(bundle, "w") as tar:
            for comp, pkg in (("common", "kidctl_common"), ("tray", "kidctl_tray")):
                tar.add(REPO / comp / pkg, arcname=f"lib/{pkg}",
                        filter=lambda ti: None if "__pycache__" in ti.name else ti)
            data = VM_SCRIPT.encode()
            info = tarfile.TarInfo("shots.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        winvm("ssh", f"Remove-Item -Recurse -Force {VM_DIR} -ErrorAction SilentlyContinue; "
                     f"New-Item -ItemType Directory {VM_DIR} | Out-Null")
        winvm("put", str(bundle), f"{VM_DIR}/winshots.tar")
        result = winvm("ssh", f"cd {VM_DIR}; tar -xf winshots.tar; & '{PYTHON}' shots.py {VM_DIR}/out {langs} {schemes}",
                       capture=True)
        if "ok" not in result.split():
            sys.exit(f"Rendern in der VM fehlgeschlagen:\n{result}")
        out.mkdir(parents=True, exist_ok=True)
        winvm("get", f"{VM_DIR}/out/.", str(out))
    print(f"Fenster: {out}")


def desktop(out: Path, name: str) -> None:
    """Bildschirmabzug der VM (QMP ``screendump``, PNG)."""
    win = os.environ.get("KIDCTL_WIN", "win11")
    sock_path = Path(os.environ.get("KIDCTL_VM_DIR", Path.home() / f".local/share/kidctl-vm-{win}")) / "qmp.sock"
    if not sock_path.exists():
        sys.exit(f"QMP-Socket {sock_path} fehlt – läuft die VM (dev/vm/winvm.sh start)?")
    target = (out / "desktop" / f"{name}.png").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    f = s.makefile("rw")
    f.readline()
    for cmd in ({"execute": "qmp_capabilities"},
                {"execute": "screendump", "arguments": {"filename": str(target), "format": "png"}}):
        f.write(json.dumps(cmd) + "\n")
        f.flush()
        reply = json.loads(f.readline())
        if "error" in reply:
            sys.exit(f"QMP: {reply['error']}")
    s.close()
    print(f"Desktop: {target}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "build" / "winshots")
    ap.add_argument("--lang", default="de,en")
    ap.add_argument("--scheme", default="light,dark")
    ap.add_argument("--desktop", metavar="NAME", help="nur Bildschirmabzug der laufenden VM")
    args = ap.parse_args()
    if args.desktop:
        desktop(args.out, args.desktop)
    else:
        windows(args.out, args.lang, args.scheme)


if __name__ == "__main__":
    main()
