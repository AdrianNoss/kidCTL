"""Programmkontrolle unter Windows: Ist ein Prozess eines Kindes erlaubt? (reine Logik, testbar)

Reihenfolge der Regeln:
1. KidCTL selbst und feste Basisordner (WebView2-Laufzeit, gemeinsame Microsoft-Dateien) – erlaubt.
2. Store-Apps (Package Family Name): freigegeben -> erlaubt; App mit Startmenü-Eintrag -> Eltern-Entscheidung;
   unbekanntes Paket (gerade installiert) -> wie eine App behandeln, der Katalog wird neu gelesen; Paket ohne
   Eintrag (Hintergrunddienst des Systems) -> erlaubt.
3. Programmdateien, die dem Kind gehören (selbst geladen/entpackt, auch in beschreibbaren Ordnern unter
   ``C:\\Windows``), sind gesperrt – außer im Ordner einer freigegebenen App im Profil (z. B. Roblox).
4. Unter ``%WINDIR%``: Werkzeuge, mit denen sich Code ausführen oder das System umbauen lässt, sind gesperrt
   (``cmd`` nur interaktiv, ``rundll32``/``regsvr32`` nur mit fremder DLL); Programme mit Startmenü-Eintrag
   (Task-Manager, Systemsteuerung …) entscheiden die Eltern; alles andere ist Basis.
5. Freigegebene Programme und ihr Programmordner (außerhalb von ``%WINDIR%``) – erlaubt; sonst gesperrt.
"""

from __future__ import annotations

import ntpath
import os
import re
from dataclasses import dataclass, field

from .catalog import norm

WINDIR = norm(os.environ.get("WINDIR", r"C:\Windows"))
PROGRAM_FILES = norm(os.environ.get("ProgramFiles", r"C:\Program Files"))
PROGRAM_FILES_X86 = norm(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
BASE_DIRS = (
    PROGRAM_FILES_X86 + r"\microsoft\edgewebview",          # WebView2-Laufzeit (von vielen Apps genutzt)
    PROGRAM_FILES + r"\common files\microsoft shared",
    PROGRAM_FILES_X86 + r"\common files\microsoft shared",
    PROGRAM_FILES + r"\windows defender",
    norm(os.environ.get("ProgramData", r"C:\ProgramData")) + r"\microsoft\windows defender\platform",
)
# Von der Aufgabenplanung bzw. einem Dienst gestartet (z. B. automatische Datenträgerbereinigung)
SYSTEM_PARENTS = {WINDIR + r"\system32\taskhostw.exe", WINDIR + r"\system32\svchost.exe"}
ESSENTIAL = {WINDIR + r"\explorer.exe"}   # hat einen Startmenü-Eintrag, ist aber die Shell
# Pakete des Systems mit (einzelnen) Startmenü-Einträgen, die zugleich Shell-Teile enthalten (Suche, Widgets,
# „Klick und los“, Geräteübergreifendes) – Basis, nicht im Katalog
BASE_PFN_PREFIXES = ("microsoftwindows.client.",)
SHELLS = {"cmd.exe"}
# Werkzeuge unter %WINDIR%, die nur mit Freigabe laufen (Code ausführen, System umbauen, Dateien laden)
SYSTEM_TOOLS = {"powershell.exe", "powershell_ise.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe",
                "regedit.exe", "regedt32.exe", "reg.exe", "mmc.exe", "msiexec.exe", "installutil.exe",
                "bitsadmin.exe", "certutil.exe", "wsl.exe", "wslhost.exe", "bash.exe", "msbuild.exe", "csc.exe",
                "vbc.exe", "jsc.exe", "regasm.exe", "regsvcs.exe", "cmstp.exe", "forfiles.exe", "pcalua.exe",
                "scriptrunner.exe", "msdt.exe", "control.exe", "taskmgr.exe", "schtasks.exe", "at.exe", "sc.exe",
                "netsh.exe", "runas.exe", "wmic.exe", "winrm.exe", "ftp.exe", "curl.exe", "tar.exe", "hh.exe"}
DLL_LOADERS = {"rundll32.exe", "regsvr32.exe"}
_INTERACTIVE_OFF = re.compile(r'(?i)(?:^|\s)/[a-z]*c(?:\s|$)')   # cmd /c, /d /c, /s /c …
_DLL_ARG = re.compile(r'(?i)"?([a-z]:\\[^",]+?\.(?:dll|ocx|cpl|ax))"?')


@dataclass(frozen=True)
class Verdict:
    key: str
    name: str
    detail: str = ""
    code: str = "not_allowed"
    arg: str = ""
    grantable: bool = True
    unknown_package: bool = False


@dataclass
class WinAllowed:
    """Was für ein Kind erlaubt ist (``keys`` in Normalform, siehe ``key_norm``)."""
    keys: set[str]
    sid: str = ""
    catalog_exes: set[str] = field(default_factory=set)   # normcase-Pfade mit Startmenü-Eintrag
    catalog_pfns: set[str] = field(default_factory=set)   # Store-Apps mit Eintrag (klein)
    known_pfns: set[str] = field(default_factory=set)     # alle Pakete (klein)
    install_dir: str = ""                                 # KidCTL selbst

    def __post_init__(self) -> None:
        self.keys = {key_norm(k) for k in self.keys}
        self.catalog_pfns = {p.lower() for p in self.catalog_pfns}
        self.known_pfns = {p.lower() for p in self.known_pfns}
        # Programmordner freigegebener Programme (außerhalb von %WINDIR%), z. B. „…\Mozilla Firefox“; bei Apps
        # im Profil der App-Ordner unter AppData (übersteht Updates in neue Versionsordner, z. B. Roblox)
        self.dirs = tuple(sorted({d for k in self.keys if k.startswith("exe:") and not _under(k[4:], WINDIR)
                                  and (d := app_root(k[4:])) not in _ROOTS}))

    @property
    def released_dirs(self) -> tuple[str, ...]:
        return self.dirs


_APPDATA = re.compile(r"^(.*\\appdata\\(?:local\\programs|local|roaming)\\[^\\]+)\\")


def app_root(path: str) -> str:
    """Ordner einer App: unter AppData der erste Ordner (``…\\AppData\\Local\\Roblox``), sonst der Programmordner."""
    path = norm(path)
    if m := _APPDATA.match(path):
        return m.group(1)
    return ntpath.dirname(path)


def key_norm(key: str) -> str:
    if key.startswith("exe:"):
        return "exe:" + norm(key[4:])
    if key.startswith("appx:"):
        return "appx:" + key[5:].lower()
    return key


_ROOTS = {PROGRAM_FILES, PROGRAM_FILES_X86, WINDIR, norm("C:\\")}


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "\\")


def classify(proc, allowed: WinAllowed, owner=lambda path: "", parent_exe=lambda: "") -> Verdict | None:
    """``None`` = erlaubt, sonst Begründung. ``owner(pfad)`` liefert die SID des Dateibesitzers,
    ``parent_exe()`` den Pfad des Elternprozesses (nur bei Bedarf gelesen)."""
    exe = norm(proc.exe)
    name = ntpath.basename(proc.exe)
    base = name.lower()
    if allowed.install_dir and _under(exe, norm(allowed.install_dir)):
        return None
    if any(_under(exe, d) for d in BASE_DIRS):
        return None
    if proc.pfn:
        pfn = proc.pfn.lower()
        key = "appx:" + proc.pfn
        if "appx:" + pfn in allowed.keys or pfn.startswith(BASE_PFN_PREFIXES):
            return None
        if _under(exe, WINDIR + r"\system32") or _under(exe, WINDIR + r"\syswow64"):
            return None   # Windows-Helfer mit App-Identität (Hintergrundaufgabe, z. B. backgroundTaskHost)
        if pfn in allowed.catalog_pfns:
            return Verdict(key, proc.pfn.split("_")[0])
        if pfn not in allowed.known_pfns:
            return Verdict(key, proc.pfn.split("_")[0], unknown_package=True)
        return None   # Paket ohne Startmenü-Eintrag: Hintergrunddienst
    key = "exe:" + proc.exe
    if "exe:" + exe in allowed.keys:
        return None
    try:
        own = bool(allowed.sid) and owner(proc.exe) == allowed.sid
    except OSError:
        own = False
    if own and not any(_under(exe, d) for d in allowed.dirs):
        return Verdict(key, name, "eigene Programmdatei", code="own_program")
    if _under(exe, WINDIR):
        if base in SHELLS:
            if _INTERACTIVE_OFF.search(_args(proc.cmdline)):
                return None   # cmd /c … von Systemkomponenten; was es startet, wird selbst geprüft
            return Verdict(key, name, "interaktive Shell", code="interactive_shell")
        if base in SYSTEM_TOOLS:
            return Verdict(key, name, "Systemwerkzeug", code="system_tool")
        if base in DLL_LOADERS:
            dll = _DLL_ARG.search(_args(proc.cmdline))
            if dll and not _trusted_dll(norm(dll.group(1))):
                return Verdict(key, name, f"fremde DLL ({ntpath.basename(dll.group(1))})", code="foreign_dll",
                               arg=ntpath.basename(dll.group(1)), grantable=False)
            return None
        if exe in allowed.catalog_exes and exe not in ESSENTIAL:
            if norm(parent_exe() or "") in SYSTEM_PARENTS:
                return None   # vom System gestartet, nicht vom Kind
            return Verdict(key, name)
        return None
    if any(_under(exe, d) for d in allowed.dirs):
        return None
    return Verdict(key, name)


def _args(cmdline: str) -> str:
    """Befehlszeile ohne das Programm selbst."""
    cmdline = cmdline.strip()
    if cmdline.startswith('"'):
        end = cmdline.find('"', 1)
        return cmdline[end + 1:] if end > 0 else ""
    return cmdline.split(" ", 1)[1] if " " in cmdline else ""


def _trusted_dll(path: str) -> bool:
    return any(_under(path, d) for d in (WINDIR, PROGRAM_FILES, PROGRAM_FILES_X86))
