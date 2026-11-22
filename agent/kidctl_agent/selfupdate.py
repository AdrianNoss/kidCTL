"""Update auf Knopfdruck (Linux): nur die eigenen Pakete aus der fest eingerichteten, signierten kidCTL-Quelle.

Der Server löst nur aus (``update_now``); Paketnamen, Quelle und Befehl stehen hier fest. Die Quelle kommt aus der
lokalen Systemkonfiguration, die ``kidctl-repo-client.sh`` angelegt hat – nie vom Server.

- **Arch:** eigene pacman-Konfiguration nur mit dem ``[kidctl]``-Abschnitt (Server aus ``/etc/pacman.conf``,
  Signaturpflicht hier fest): ``-Sy`` frischt nur ``kidctl.db`` auf, die übrigen Sync-Datenbanken bleiben
  unberührt (kein Teil-Upgrade-Zustand für andere Pakete).
- **Debian/Ubuntu:** ``apt-get update`` nur für ``kidctl.sources`` (``Signed-By``), dann
  ``apt-get install --only-upgrade``.

Der Paketmanager läuft als eigene transiente systemd-Unit (``kidctl-update``): Das Paket-Update startet den Agenten
neu (``try-restart``) – liefe pacman/apt im Dienst des Agenten, würde es dabei mitten in der Transaktion beendet.
Außerdem gelten dort die Einschränkungen des Agenten-Dienstes nicht (``ProtectSystem``).
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("kidctl.update")

PACKAGES = ("kidctl", "kidctl-plasma", "kidctl-gnome")
PACMAN_CONF = Path("/etc/pacman.conf")
APT_SOURCES = Path("/etc/apt/sources.list.d/kidctl.sources")
WORK_DIR = Path("/run/kidctl/update")   # übersteht den Neustart des Agenten (RuntimeDirectoryPreserve)
UNIT = "kidctl-update"
MAX_RUNTIME = 1800                      # s, danach beendet systemd den Paketmanager
SERVER_LINE = re.compile(r"^https?://\S+$")


class UpdateError(Exception):
    """Update nicht möglich; Text geht an die Eltern-WebUI."""


def repo_servers(conf_text: str) -> list[str]:
    """``Server``-Einträge des ``[kidctl]``-Abschnitts einer pacman.conf (nur http(s)-Adressen)."""
    servers, section = [], None
    for raw in conf_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
        elif section == "kidctl" and "=" in line:
            key, value = (x.strip() for x in line.split("=", 1))
            if key == "Server" and SERVER_LINE.match(value):
                servers.append(value)
    return servers


def pacman_config(conf_text: str) -> str:
    servers = repo_servers(conf_text)
    if not servers:
        raise UpdateError(f"kidCTL-Repository nicht in {PACMAN_CONF} eingetragen")
    return ("# erzeugt vom kidctl-Agenten für das Update auf Knopfdruck – nur das kidCTL-Repository\n"
            "[options]\nArchitecture = auto\nSigLevel = Required DatabaseRequired\n\n"
            "[kidctl]\nSigLevel = Required DatabaseRequired\n" + "".join(f"Server = {s}\n" for s in servers))


def _systemd_run(*cmd: str, env: dict[str, str] | None = None) -> list[str]:
    return ["systemd-run", f"--unit={UNIT}", "--collect", "--wait", "--quiet", "--service-type=exec",
            f"--property=RuntimeMaxSec={MAX_RUNTIME}",
            *(f"--setenv={k}={v}" for k, v in (env or {}).items()), "--", *cmd]


class SelfUpdater:
    """Plattformerkennung und Ausführung; ``run`` austauschbar (Tests)."""

    def __init__(self, run=subprocess.run, which=shutil.which, pacman_conf: Path = PACMAN_CONF,
                 apt_sources: Path = APT_SOURCES, work_dir: Path = WORK_DIR) -> None:
        self.run, self.which = run, which
        self.pacman_conf, self.apt_sources, self.work_dir = pacman_conf, apt_sources, work_dir

    def _ok(self, *cmd: str) -> bool:
        try:
            return self.run(list(cmd), capture_output=True, text=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def command(self) -> list[str]:
        """Befehl für die transiente Unit; wirft ``UpdateError``, wenn die Quelle fehlt."""
        if self.which("pacman"):
            try:
                text = self.pacman_conf.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                raise UpdateError(f"{self.pacman_conf} nicht lesbar: {e}") from e
            self.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            conf = self.work_dir / "pacman.conf"
            conf.write_text(pacman_config(text), encoding="utf-8")
            conf.chmod(0o600)
            pkgs = [n for n in PACKAGES if self._ok("pacman", "-Q", n)]
            if "kidctl" not in pkgs:
                raise UpdateError("Paket kidctl nicht über pacman installiert")
            return _systemd_run("pacman", "--config", str(conf), "-Sy", "--needed", "--noconfirm", *pkgs)
        if self.which("apt-get"):
            if not self.apt_sources.is_file() or "Signed-By:" not in self.apt_sources.read_text(errors="replace"):
                raise UpdateError(f"kidCTL-Paketquelle {self.apt_sources} fehlt oder ist nicht signiert")
            pkgs = [n for n in PACKAGES if self._installed_deb(n)]
            if "kidctl" not in pkgs:
                raise UpdateError("Paket kidctl nicht über apt installiert")
            update = ["apt-get", "update", "-o", f"Dir::Etc::sourcelist={self.apt_sources}",
                      "-o", "Dir::Etc::sourceparts=-", "-o", "APT::Get::List-Cleanup=0"]
            install = ["apt-get", "install", "-y", "--only-upgrade", "-o", "Dpkg::Options::=--force-confdef",
                       "-o", "Dpkg::Options::=--force-confold", *pkgs]
            return _systemd_run("/bin/sh", "-c", f"{shlex.join(update)} && {shlex.join(install)}",
                                env={"DEBIAN_FRONTEND": "noninteractive"})
        raise UpdateError("weder pacman noch apt-get gefunden")

    def _installed_deb(self, name: str) -> bool:
        try:
            r = self.run(["dpkg-query", "-W", "-f=${Status}", name], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0 and r.stdout.strip().endswith("installed") and "not-installed" not in r.stdout

    def __call__(self) -> tuple[str, str]:
        """Blockierend (im Thread aufrufen). Läuft das Update durch, startet das Paket den Agenten neu, bevor
        dieser Aufruf zurückkehrt; kehrt er erfolgreich zurück, gab es nichts zu aktualisieren."""
        try:
            cmd = self.command()
        except UpdateError as e:
            return "failed", str(e)
        started = time.time()
        log.warning("Update auf Knopfdruck: %s", shlex.join(cmd))
        try:
            r = self.run(cmd, capture_output=True, text=True, timeout=MAX_RUNTIME + 60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return "failed", f"systemd-run: {e}"
        if r.returncode == 0:
            return "current", ""
        return "failed", self._journal_tail(started) or f"Paketmanager beendet mit Status {r.returncode}"

    def _journal_tail(self, since: float) -> str:
        try:
            r = self.run(["journalctl", "-u", f"{UNIT}.service", "--since", f"@{int(since) - 1}", "-o", "cat",
                          "-n", "3", "-q", "--no-pager"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return " / ".join(x.strip() for x in r.stdout.splitlines() if x.strip())[-300:]
