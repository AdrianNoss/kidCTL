"""Programm-Whitelist: Entscheidung je Prozess (ohne I/O, testbar).

Reihenfolge:
1. Flatpak (erkannt über die Cgroup ``app-flatpak-<id>-*.scope``): nur freigegebene App-IDs.
2. Programm selbst freigegeben (Basisliste, Eltern-Liste oder im Verzeichnis einer freigegebenen
   App, z. B. Chromium-Hilfsprozesse) -> erlaubt.
3. Shells: ``-c befehl`` oder Skriptdatei aus Systemverzeichnissen erlaubt; interaktiv (liest vom
   Terminal/stdin, z. B. TTY-Login) oder Skript aus dem Home -> gesperrt. Ein Steuerterminal allein
   sagt nichts: SDDM startet die Sitzung über ``bash --login wayland-session`` auf dem Sitzungs-TTY.
4. Interpreter (python, perl, …): nur mit Skriptdatei, die selbst freigegeben ist;
   ``-c``/``-m``/``-e``, stdin oder interaktiv -> gesperrt.
5. Steam (nur als Ganzes freigebbar): Programme aus der Steam-Bibliothek ``/srv/steam/<user>/``
   nur, wenn Steam freigegeben ist und der Prozess von Steam abstammt (Elternkette); die
   Einstiegspunkte ``steam.sh`` und ``ubuntu12_32/steam`` selbst sind dann erlaubt (der Starter
   ruft ``steam.sh`` per exec auf – danach gibt es keinen Steam-Vorfahren). Ist Steam
   freigegeben, sind auch Nachfahren von Steam außerhalb der Bibliothek erlaubt (Laufzeit-
   Container, Proton) – bekannte Restlücke „Nicht-Steam-Spiel hinzufügen“.
6. Sonst gesperrt.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .procs import ProcInfo

BASE_FILE = Path(os.environ.get("KIDCTL_BASE_PROGRAMS", "/usr/lib/kidctl/base-programs.txt"))

SHELLS = {"sh", "bash", "dash", "zsh", "fish", "ksh", "mksh", "tcsh", "csh", "busybox"}
INTERPRETER = re.compile(
    r"^(python[0-9.]*|perl[0-9.]*|ruby[0-9.]*|irb|node|nodejs|lua[0-9.]*|luajit|php[0-9.]*"
    r"|tclsh[0-9.]*|wish[0-9.]*|guile[0-9.]*|Rscript|julia)$")
# Optionen, die Code direkt ausführen bzw. interaktiv machen (je Interpreter, Einbuchstaben-Flags)
INLINE_FLAGS = {"python": "cmi", "perl": "eE", "ruby": "e", "irb": "", "node": "epi", "nodejs": "epi",
                "lua": "ei", "luajit": "ei", "php": "ra", "Rscript": "e", "julia": "ei"}
INLINE_LONG = {"--eval", "--print", "--interactive", "--command", "--module"}
# Optionen mit separatem Wert (überspringen)
VALUE_OPTS = {"-W", "-X", "-I", "-r", "--require", "-l"}
# Skripte aus diesen Verzeichnissen gehören root – das Kind kann dort nichts ablegen
SYSTEM_SCRIPT_DIRS = ("/usr/", "/etc/", "/opt/")
# Shell-Optionen mit separatem Wert
SHELL_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}

# Verzeichnisse, in denen viele fremde Programme liegen – dort keine „App-Verzeichnis“-Regel
GENERIC_DIRS = {"/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin", "/usr/local/sbin",
                "/usr/lib", "/usr/lib64", "/usr/libexec", "/usr/local/lib", "/opt", "/usr/share"}

# Steam: Systemteil (Bootstrap) und Client innerhalb der Bibliothek
STEAM_SYSTEM = "/usr/lib/steam/"
STEAM_CLIENT = "ubuntu12_32/steam"
# Protokoll-Eintrag, wenn Steam nicht freigegeben ist: Schlüssel des Steam-Starters aus dem Katalog,
# damit Eltern sehen (und freigeben), worum es geht – statt einzelner Hilfsprogramme wie cmp
STEAM_KEY = "exe:/usr/bin/steam"
STEAM_SCRIPT = "steam.sh"
MAX_ANCESTORS = 64

# Immer erlaubt (kidctl selbst) bzw. nie in der Basis (Loader startet beliebige Programme)
BUILTIN_BASE = ["/usr/lib/kidctl/*"]
BUILTIN_EXCLUDE = ["/usr/lib/ld-linux*", "/usr/lib64/ld-linux*", "/usr/lib/ld-*.so*"]
EXEC_DIRS = ("/usr/bin/", "/usr/lib/")


@dataclass
class BaseSpec:
    """Inhalt von base-programs.txt: Wurzelpakete, zusätzliche Muster, Ausschlüsse."""
    packages: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)   # Paketgruppen: alle installierten Mitglieder
    globs: list[str] = field(default_factory=lambda: list(BUILTIN_BASE))
    excludes: list[str] = field(default_factory=lambda: list(BUILTIN_EXCLUDE))


def load_base(path: Path = BASE_FILE) -> BaseSpec:
    spec = BaseSpec()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return spec
    for ln in (x.strip() for x in lines):
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("pkg:"):
            spec.packages.append(ln[4:])
        elif ln.startswith("group:"):
            spec.groups.append(ln[6:])
        elif ln.startswith("!"):
            spec.excludes.append(ln[1:])
        else:
            spec.globs.append(ln)
    return spec


def _qi_blocks(text: str):
    """``pacman -Qi`` (LC_ALL=C) -> Felder je Paket (Fortsetzungszeilen angehängt)."""
    for block in text.strip().split("\n\n"):
        fields: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            m = re.match(r"^(\S[^:]*?)\s*: (.*)$", line)
            if m:
                key = m.group(1)
                fields[key] = m.group(2)
            elif key:
                fields[key] += " " + line.strip()
        if fields.get("Name"):
            yield fields


def _names(v: str) -> list[str]:
    return [re.split(r"[<>=]", x)[0] for x in v.split()] if v and v != "None" else []


def parse_pacman_qi(text: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """``pacman -Qi`` (LC_ALL=C) -> (Paket -> Abhängigkeiten, bereitgestellter Name -> Paket)."""
    deps: dict[str, list[str]] = {}
    provides: dict[str, str] = {}
    for fields in _qi_blocks(text):
        name = fields["Name"]
        deps[name] = _names(fields.get("Depends On", ""))
        for prov in _names(fields.get("Provides", "")):
            provides.setdefault(prov, name)
    return deps, provides


def group_members(text: str, groups: list[str]) -> list[str]:
    """Installierte Pakete, die zu einer der ``groups`` gehören (Feld „Groups“ von ``pacman -Qi``)."""
    wanted = set(groups)
    return sorted(f["Name"] for f in _qi_blocks(text) if wanted & set(_names(f.get("Groups", ""))))


def closure(deps: dict[str, list[str]], provides: dict[str, str], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = [r for r in roots if r in deps]
    while stack:
        pkg = stack.pop()
        if pkg in seen:
            continue
        seen.add(pkg)
        for d in deps.get(pkg, []):
            d = d if d in deps else provides.get(d)
            if d and d not in seen:
                stack.append(d)
    return seen


def is_shell_or_interpreter(path: str) -> bool:
    name = os.path.basename(path)
    return name in SHELLS or re.sub(r"[0-9.]+$", "", name) in SHELLS or bool(INTERPRETER.match(name))


def base_paths(files: list[str], app_exes: set[str], excludes: list[str]) -> frozenset[str]:
    """Ausführbare Dateien der Paketkette, ohne Apps (Eltern-Entscheidung), Shells/Interpreter,
    Bibliotheken und Ausschlüsse."""
    out = set()
    for f in files:
        if not f.startswith(EXEC_DIRS) or re.search(r"\.so(\.|$)", f):
            continue
        if f in app_exes or is_shell_or_interpreter(f):
            continue
        if any(fnmatch.fnmatchcase(f, pat) for pat in excludes):
            continue
        out.add(f)
    return frozenset(out)


@dataclass
class Allowed:
    base: list[str] = field(default_factory=lambda: list(BUILTIN_BASE))  # Glob-Muster
    keys: set[str] = field(default_factory=set)                           # Eltern-Freigaben
    base_exact: frozenset[str] = frozenset()                              # aus der Paketkette
    excludes: list[str] = field(default_factory=lambda: list(BUILTIN_EXCLUDE))
    steam_root: str = ""   # z. B. "/srv/steam/kind/" (Steam-Bibliothek dieses Kindes)

    def __post_init__(self) -> None:
        self.exes = {k[4:] for k in self.keys if k.startswith("exe:")}
        # Steam gilt als freigegeben, wenn der Steam-Starter freigegeben ist
        self.steam_enabled = any(e.startswith(STEAM_SYSTEM) or e == "/usr/bin/steam" for e in self.exes)
        self.flatpaks = {k[8:] for k in self.keys if k.startswith("flatpak:")}
        self.app_dirs = {d for d in (os.path.dirname(e) for e in self.exes) if d not in GENERIC_DIRS}

    def path_ok(self, path: str) -> bool:
        if path in self.exes:  # ausdrückliche Eltern-Freigabe gilt immer
            return True
        if any(path.startswith(d + "/") for d in self.app_dirs):
            return True
        if any(fnmatch.fnmatchcase(path, pat) for pat in self.excludes):
            return False
        return path in self.base_exact or any(fnmatch.fnmatchcase(path, pat) for pat in self.base)


@dataclass(frozen=True)
class Verdict:
    key: str      # App-Schlüssel für Protokoll/Freigabe
    name: str
    detail: str = ""


def script_arg(interp: str, argv: tuple[str, ...]) -> str | None:
    """Skriptdatei eines Interpreter-Aufrufs; ``None`` bei Inline-Code, Modul, stdin, REPL."""
    family = re.sub(r"[0-9.]+$", "", interp)
    letters = INLINE_FLAGS.get(family, "ce")
    args = list(argv[1:])
    while args:
        a = args.pop(0)
        if a == "--":
            return args[0] if args else None
        if a == "-":
            return None
        if a.startswith("--"):
            if a.split("=", 1)[0] in INLINE_LONG:
                return None
            if a in VALUE_OPTS and args:
                args.pop(0)
            continue
        if a.startswith("-"):
            if any(ch in letters for ch in a[1:]):
                return None
            if a in VALUE_OPTS and args:
                args.pop(0)
            continue
        return a
    return None


def shell_mode(argv: tuple[str, ...]) -> tuple[str, str | None]:
    """("command", None) bei ``-c``, ("script", pfad) bei Skriptdatei, sonst ("interactive", None)."""
    args = list(argv[1:])
    while args:
        a = args.pop(0)
        if a == "--":
            return ("script", args[0]) if args else ("interactive", None)
        if a in SHELL_VALUE_OPTS:
            if args:
                args.pop(0)
            continue
        if a.startswith("--"):
            continue  # --login, --norc, --posix …
        if a[:1] in "-+" and len(a) > 1:
            letters = a[1:]
            if "c" in letters:
                return ("command", None)
            if "s" in letters or "i" in letters:
                return ("interactive", None)
            continue
        return ("script", a)
    return ("interactive", None)


def _program_path(info: ProcInfo) -> str:
    """Programmdatei eines Prozesses: bei Shells/Interpretern die Skriptdatei, sonst das Binary."""
    name = os.path.basename(info.exe)
    script = None
    if name in SHELLS or re.sub(r"[0-9.]+$", "", name) in SHELLS:
        mode, script = shell_mode(info.argv)
    elif INTERPRETER.match(name):
        script = script_arg(name, info.argv)
    if script and os.path.isabs(script):
        return os.path.realpath(script)
    return info.exe


def is_steam_process(info: ProcInfo, steam_root: str) -> bool:
    path = _program_path(info)
    return (path.startswith(STEAM_SYSTEM) or path == "/usr/bin/steam"
            or (bool(steam_root) and path in (steam_root + STEAM_CLIENT, steam_root + STEAM_SCRIPT)))


def classify(info: ProcInfo, allowed: Allowed,
             ancestors: Callable[[], Iterable[ProcInfo]] | None = None) -> Verdict | None:
    """``None`` = erlaubt, sonst ``Verdict`` (was gesperrt wird). ``ancestors`` liefert die
    Elternkette (nur für Steam gebraucht, daher lazy)."""
    if info.flatpak_id:
        if info.flatpak_id in allowed.flatpaks:
            return None
        return Verdict(f"flatpak:{info.flatpak_id}", info.flatpak_id)

    exe = info.exe
    name = os.path.basename(exe)

    def from_steam() -> bool:
        return ancestors is not None and any(
            is_steam_process(a, allowed.steam_root) for _, a in zip(range(MAX_ANCESTORS), ancestors()))

    def steam_blocked() -> Verdict:
        return Verdict(STEAM_KEY, "Steam", f"nicht freigegeben – {os.path.basename(path)}")

    path = _program_path(info)
    if allowed.steam_root and path.startswith(allowed.steam_root):
        if not allowed.steam_enabled:
            return steam_blocked()
        if is_steam_process(info, allowed.steam_root) or from_steam():
            return None
        return Verdict(f"exe:{path}", os.path.basename(path), "Steam-Bibliothek (über Steam gestartet nötig)")

    if allowed.path_ok(exe):
        return None
    if from_steam():
        if allowed.steam_enabled:
            return None  # von Steam gestartet (Laufzeit-Container, Proton, …)
        return steam_blocked()  # Hilfsprogramm des Steam-Starters (z. B. cmp)

    if name in SHELLS or re.sub(r"[0-9.]+$", "", name) in SHELLS:
        mode, script = shell_mode(info.argv)
        if mode == "command":
            return None
        if mode == "script":
            real = os.path.realpath(script) if os.path.isabs(script) else script
            if real.startswith(SYSTEM_SCRIPT_DIRS) or allowed.path_ok(real):
                return None
            return Verdict(f"exe:{real}", os.path.basename(real), f"Skript ({name})")
        return Verdict(f"exe:{exe}", name, "interaktive Shell")

    if INTERPRETER.match(name):
        script = script_arg(name, info.argv)
        if script is None:
            return Verdict(f"exe:{exe}", name, "Interpreter ohne freigegebenes Skript")
        real = os.path.realpath(script) if os.path.isabs(script) else script
        if os.path.isabs(real) and allowed.path_ok(real):
            return None
        return Verdict(f"exe:{real}", os.path.basename(real), f"Skript ({name})")

    return Verdict(f"exe:{exe}", name)
