"""Automatisches Selbst-Update unter Windows.

Der Server liefert unter ``<Server>/repo/windows/`` den Installer und ``latest.json`` mit Signatur
``latest.json.sig`` (Ed25519, Base64). Der öffentliche Schlüssel liegt neben diesem Modul (``update-key.pub``,
vom Bau-Skript eingefügt); ohne Schlüssel gibt es keine Updates.

Ablauf: alle 6 Stunden (und kurz nach dem Start) ``latest.json`` holen, Signatur prüfen, bei neuerer Version
den Installer laden, SHA-256 aus dem signierten Manifest prüfen und still ausführen (der Installer beendet und
startet den Dienst). Schlägt ein Update fehl, wird dieselbe Version erst nach ``RETRY`` erneut versucht.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from kidctl_common import ed25519

from .. import __version__, store, tls

log = logging.getLogger("kidctl.update")

KEY_FILE = Path(__file__).with_name("update-key.pub")
UPDATE_DIR = store.PROGRAM_DATA / "updates"
MANIFEST = "repo/windows/latest.json"
STATE_FILE = "update.json"
CHECK_EVERY = 6 * 3600.0
FIRST_CHECK = 120.0
RETRY = 24 * 3600.0
MAX_MANIFEST = 64 * 1024
MAX_INSTALLER = 500 * 1024 * 1024
FILE_PATTERN = re.compile(r"^KidCTL-Setup-\d+(?:\.\d+)*\.exe$")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def public_key() -> bytes | None:
    try:
        raw = KEY_FILE.read_text().strip()
        return bytes.fromhex(raw) if raw else None
    except (OSError, ValueError):
        return None


def parse_manifest(data: bytes, signature_b64: bytes, key: bytes) -> dict:
    """Manifest prüfen und lesen; wirft ``ValueError`` bei falscher Signatur oder ungültigem Inhalt."""
    try:
        sig = base64.b64decode(signature_b64.strip(), validate=True)
    except ValueError as e:
        raise ValueError("Signatur nicht lesbar") from e
    if not ed25519.verify(key, data, sig):
        raise ValueError("Signatur ungültig")
    m = json.loads(data)
    if not (isinstance(m, dict) and isinstance(m.get("version"), str) and re.fullmatch(r"\d+(\.\d+)*", m["version"])
            and isinstance(m.get("file"), str) and FILE_PATTERN.match(m["file"])
            and isinstance(m.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", m["sha256"])):
        raise ValueError("Manifest ungültig")
    return m


class Updater:
    def __init__(self, device: dict | None, clock=time.monotonic, opener=tls.urlopen) -> None:
        self.base = (device or {}).get("server_url", "").rstrip("/")
        if not self.base.startswith("https://") and not (device or {}).get("insecure_http"):
            self.base = ""   # wie die Serververbindung: ohne TLS nur mit --insecure-http (Test-Server)
        self.clock = clock
        self.opener = opener
        self.key = public_key()
        self._next = clock() + FIRST_CHECK
        if not self.key:
            log.warning("Kein Update-Schlüssel (%s) – automatische Updates aus", KEY_FILE.name)

    def _get(self, url: str, limit: int) -> bytes:
        with self.opener(url, timeout=60) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"{url}: zu groß")
        return data

    def due(self) -> bool:
        return bool(self.key and self.base) and self.clock() >= self._next

    def check(self) -> str | None:
        """Prüft und startet ggf. das Update (blockierend, im Thread aufrufen). Rückgabe: gestartete Version."""
        self._next = self.clock() + CHECK_EVERY
        url = f"{self.base}/{MANIFEST}"
        try:
            manifest = parse_manifest(self._get(url, MAX_MANIFEST), self._get(url + ".sig", 1024), self.key)
        except Exception as e:   # Netzwerk, Signatur, Inhalt
            log.warning("Update-Prüfung fehlgeschlagen: %s", e)
            return None
        version = manifest["version"]
        if version_key(version) <= version_key(__version__):
            return None
        state = store.load(STATE_FILE) or {}
        if state.get("version") == version and time.time() - state.get("at", 0) < RETRY:
            return None   # gerade erst versucht
        store.save(STATE_FILE, {"version": version, "at": time.time()})
        try:
            data = self._get(f"{self.base}/repo/windows/{manifest['file']}", MAX_INSTALLER)
            if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                raise ValueError("Prüfsumme des Installers falsch")
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            path = UPDATE_DIR / manifest["file"]
            path.write_bytes(data)
        except Exception as e:
            log.error("Update %s nicht geladen: %s", version, e)
            return None
        log.warning("Update %s -> %s wird installiert", __version__, version)
        self.launch(path)
        return version

    def launch(self, installer: Path) -> None:
        if sys.platform != "win32":
            return
        log_file = UPDATE_DIR / "install.log"
        args = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log_file}"]
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:   # aus einem Job (falls vorhanden) lösen, sonst endet der Installer mit dem Dienst
            subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, close_fds=True, cwd=str(UPDATE_DIR))
        except OSError:
            subprocess.Popen(args, creationflags=flags, close_fds=True, cwd=str(UPDATE_DIR))
