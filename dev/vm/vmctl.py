#!/usr/bin/env python3
"""Test-VM über QMP bedienen: Text tippen (deutsches Tastaturlayout), Tasten, Mausklick, Screenshot.

    vmctl.py SOCKET type "text"        Text tippen (\\n = Enter)
    vmctl.py SOCKET key ctrl+alt+delete
    vmctl.py SOCKET click X Y          Klick; Koordinaten wie im Screenshot (Breite 900)
    vmctl.py SOCKET shot DATEI         Screenshot als DATEI.png (Breite 900, braucht PySide6)

SOCKET: QMP-Socket der VM, z. B. ~/.local/share/kidctl-vm-win11/qmp.sock
"""

import json
import os
import socket
import sys
import tempfile
import time

WIDTH = 900
DE = {"y": "z", "z": "y"}
# Zeichen -> (Modifikator, Taste) im deutschen Layout
SPECIAL = {":": ("shift", "dot"), ".": ("", "dot"), "\\": ("alt_r", "minus"), "%": ("shift", "5"),
           "/": ("shift", "7"), "(": ("shift", "8"), ")": ("shift", "9"), "-": ("", "slash"), "_": ("shift", "slash"),
           '"': ("shift", "2"), "=": ("shift", "0"), "@": ("alt_r", "q"), "*": ("shift", "bracket_right"),
           "+": ("", "bracket_right"), "|": ("alt_r", "less"), ">": ("shift", "less"), "<": ("", "less"),
           "$": ("shift", "4"), "&": ("shift", "6"), "?": ("shift", "minus"), "'": ("shift", "backslash"),
           "#": ("", "backslash"), "{": ("alt_r", "7"), "}": ("alt_r", "0"), "[": ("alt_r", "8"), "]": ("alt_r", "9"),
           ";": ("shift", "comma"), ",": ("", "comma"), "~": ("alt_r", "bracket_right"), "!": ("shift", "1"),
           "\n": ("", "ret"), "\t": ("", "tab"), " ": ("", "spc")}


class Qmp:
    def __init__(self, path: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(os.path.expanduser(path))
        self.f = self.sock.makefile("rw")
        self.f.readline()
        self.cmd("qmp_capabilities")

    def cmd(self, execute: str, **arguments):
        msg = {"execute": execute}
        if arguments:
            msg["arguments"] = arguments
        self.f.write(json.dumps(msg) + "\n")
        self.f.flush()
        while True:
            reply = json.loads(self.f.readline())
            if "event" not in reply:
                return reply

    def keys(self, *names: str) -> None:
        self.cmd("send-key", keys=[{"type": "qcode", "data": n} for n in names if n])
        time.sleep(0.08)

    def type(self, text: str) -> None:
        for ch in text:
            if ch in SPECIAL:
                self.keys(*SPECIAL[ch])
            else:
                key = DE.get(ch.lower(), ch.lower())
                self.keys("shift" if ch.isupper() else "", key)

    def screendump(self, png: str):
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as tmp:
            ppm = os.path.join(tmp, "shot.ppm")
            self.cmd("screendump", filename=ppm)
            img = QImage(ppm)
        if png:
            img.scaledToWidth(WIDTH).save(png)
        return img

    def click(self, x: float, y: float) -> None:
        img = self.screendump("")
        scale = img.width() / WIDTH
        ax = int(x * scale / img.width() * 32767)
        ay = int(y * scale / img.height() * 32767)
        self.cmd("input-send-event", events=[{"type": "abs", "data": {"axis": "x", "value": ax}},
                                             {"type": "abs", "data": {"axis": "y", "value": ay}}])
        time.sleep(0.2)
        for down in (True, False):
            self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": down, "button": "left"}}])
            time.sleep(0.1)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    q, cmd, args = Qmp(sys.argv[1]), sys.argv[2], sys.argv[3:]
    if cmd == "type":
        q.type(args[0])
    elif cmd == "key":
        q.keys(*args[0].split("+"))
    elif cmd == "click":
        q.click(float(args[0]), float(args[1]))
    elif cmd == "shot":
        q.screendump(args[0] + ".png")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
