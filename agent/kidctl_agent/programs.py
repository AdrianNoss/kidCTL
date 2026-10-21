"""Programm-Whitelist: Entscheidung je Prozess (ohne I/O, testbar).

Reihenfolge:
1. Flatpak (erkannt über die Cgroup ``app-flatpak-<id>-*.scope``): nur freigegebene App-IDs.
2. Programm selbst freigegeben (Basisliste, Eltern-Liste oder im Verzeichnis einer freigegebenen
   App, z. B. Chromium-Hilfsprozesse) -> erlaubt.
3. Shells: ``-c befehl`` oder Skriptdatei aus Systemverzeichnissen erlaubt; interaktiv (liest vom
   Terminal/stdin, z. B. TTY-Login) oder Skript aus dem Home -> gesperrt. Ein Steuerterminal allein
   sagt nichts: SDDM startet die Sitzung über ``bash --login wayland-session`` auf dem Sitzungs-TTY.
4. Interpreter (python, perl, …): nur mit Skriptdatei, die selbst freigegeben ist;
   ``-c``/``-m``/``-e``, stdin oder interaktiv -> gesperrt. Ebenso, wenn Code über die Umgebung
   (``PYTHONPATH``, ``PERL5OPT``, ``NODE_OPTIONS``, …) oder automatisch geladene Dateien im Home
   (Python-User-Site: ``.pth``, ``usercustomize.py``, überdeckte Module) eingeschleust werden kann.
5. Steam (nur als Ganzes freigebbar): Programme aus der Steam-Bibliothek ``/srv/steam/<user>/``
   nur, wenn Steam freigegeben ist und der Prozess von Steam abstammt (Elternkette); die
   Einstiegspunkte ``steam.sh`` und ``ubuntu12_32/steam`` selbst sind dann erlaubt (der Starter
   ruft ``steam.sh`` per exec auf – danach gibt es keinen Steam-Vorfahren). Ist Steam
   freigegeben, sind auch Nachfahren von Steam außerhalb der Bibliothek erlaubt (Laufzeit-
   Container, Proton) – bekannte Restlücke „Nicht-Steam-Spiel hinzufügen“.
6. Spiele-Launcher (Heroic, Lutris): Nachfahren eines freigegebenen Launchers sind erlaubt (Wine,
   Spiele) – gleiche Restlücke wie bei Steam („Spiel hinzufügen“).
7. Electron (``/usr/lib/electronNN/``): Hauptprozess erlaubt, wenn seine App (z. B.
   ``/usr/lib/heroic/app.asar``) freigegeben ist – über den Paket-Ordner einer freigegebenen App –
   und kein Code eingeschleust wird (``-r``, ``--inspect``, ``ELECTRON_RUN_AS_NODE``, ``NODE_OPTIONS``);
   Hilfsprozesse (Renderer, GPU, Crashpad) erlaubt, wenn sie von so einem Hauptprozess abstammen.
8. Sonst gesperrt.

Container (eigener Mount-Namespace, z. B. bwrap/unshare): Dort kann unter einem Systempfad eine ganz
andere Datei liegen. Für solche Prozesse wird deshalb die tatsächlich laufende Datei (bzw. das Skript
oder die Electron-App) mit der Systemdatei unter demselben Pfad verglichen (Gerät + Inode). Weicht sie
ab, ist der Prozess nur über Steam oder einen Spiele-Launcher erlaubt (deren Laufzeit-Container).

Basisliste: ausführbare Dateien der Plasma-Paketkette (``base-programs.txt``) plus Programme, die
installierte Pakete als Hintergrunddienst der Sitzung eintragen (systemweiter Autostart, D-Bus- und
systemd-User-Dienste – nur root-eigene Dateien, nie aus dem Home). Apps mit sichtbarem Menüeintrag,
Shells/Interpreter und Ausschlüsse bleiben außen vor. Der dynamische Loader (``ld-linux*``) ist nie
Basis; erlaubt ist er nur im reinen Prüfmodus von ``ldd`` (führt nichts aus).

Paket-Ordner: Verzeichnisse, die nur dem Paket einer freigegebenen App gehören (z. B.
``/usr/lib/heroic`` für ``/usr/bin/heroic``), gelten wie das Verzeichnis der App selbst.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import stat
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

# Spiele-Launcher: was sie starten (Wine, Spiele), ist erlaubt, wenn der Launcher freigegeben ist
LAUNCHERS = {"/usr/bin/heroic": "Heroic", "/usr/bin/lutris": "Lutris"}

# Electron-Laufzeit aus den Arch-Paketen electron, electron37, …
ELECTRON_DIR = re.compile(r"^/usr/lib/electron[0-9]*/")
# Optionen, mit denen Electron fremden Code lädt oder sich fernsteuern lässt
ELECTRON_UNSAFE = {"-r", "--require", "-e", "--eval", "-p", "--print", "-i", "--interactive"}
ELECTRON_UNSAFE_PREFIX = ("--inspect", "--remote-debugging", "--js-flags")
ELECTRON_UNSAFE_ENV = ("ELECTRON_RUN_AS_NODE", "NODE_OPTIONS")

# Interpreter: Variablen, die selbst Code/Optionen enthalten (gesetzt = gesperrt)
INTERP_CODE_ENV = {"PYTHONINSPECT", "PYTHONSTARTUP", "PERL5OPT", "PERL5DB", "RUBYOPT", "NODE_OPTIONS",
                   "LUA_INIT", "LUA_INIT_5_1", "LUA_INIT_5_2", "LUA_INIT_5_3", "LUA_INIT_5_4", "LUA_INIT_5_5",
                   "R_PROFILE", "R_PROFILE_USER", "PHPRC", "PHP_INI_SCAN_DIR", "JULIA_DEPOT_PATH"}
# Suchpfade für Module: nur Systemverzeichnisse erlaubt (leerer Eintrag = aktuelles Verzeichnis)
INTERP_PATH_ENV = {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONPLATLIBDIR", "PERL5LIB", "PERLLIB",
                   "RUBYLIB", "NODE_PATH", "TCLLIBPATH", "GUILE_LOAD_PATH", "GUILE_LOAD_COMPILED_PATH",
                   "JULIA_LOAD_PATH", "R_LIBS", "R_LIBS_USER", "LUA_PATH", "LUA_CPATH", "LUA_PATH_5_4",
                   "LUA_CPATH_5_4"}

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


# Hintergrunddienste installierter Pakete: (Verzeichnis, Endung, Schlüssel, Abschnitt)
SERVICE_SOURCES = (("/etc/xdg/autostart", ".desktop", "Exec", "Desktop Entry"),
                   ("/usr/share/dbus-1/services", ".service", "Exec", "D-BUS Service"),
                   ("/usr/lib/systemd/user", ".service", "ExecStart", "Service"),
                   ("/etc/systemd/user", ".service", "ExecStart", "Service"))
MAX_SERVICE_FILE = 64 * 1024


def _service_commands(text: str, key: str, section: str) -> list[str]:
    """Befehlszeilen ``key=`` im Abschnitt ``[section]``; deaktivierte Autostarts (Hidden=true) entfallen."""
    current, cmds, hidden = "", [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            current = line.strip("[]")
        elif current == section and "=" in line:
            k, v = (x.strip() for x in line.split("=", 1))
            if k == key and v:
                cmds.append(v)
            elif k == "Hidden" and v.lower() == "true":
                hidden = True
    return [] if hidden else cmds


def _command_exe(cmd: str, path_dirs: tuple[str, ...] = ("/usr/bin",)) -> str | None:
    """Programmdatei einer Befehlszeile (systemd-Präfixe ``-@:+!`` und ``env VAR=…`` übersprungen)."""
    try:
        words = shlex.split(cmd.lstrip("-@:+!"))
    except ValueError:
        return None
    if words and os.path.basename(words[0]) == "env":
        words = words[1:]
        while words and "=" in words[0]:
            words = words[1:]
        if words and words[0].startswith("-"):
            return None  # env-Optionen (z. B. -u NAME) nicht auswerten
    if not words:
        return None
    exe = words[0]
    if not os.path.isabs(exe):
        exe = next((os.path.join(d, exe) for d in path_dirs if os.path.isfile(os.path.join(d, exe))), "")
    if not exe:
        return None
    real = os.path.realpath(exe)
    return real if os.path.isfile(real) and os.access(real, os.X_OK) else None


def service_exes(sources=SERVICE_SOURCES, owner: int = 0) -> set[str]:
    """Programme, die installierte Pakete als Hintergrunddienst der Sitzung starten (kdeconnectd,
    obexd, Tray-Programme …). Nur root-eigene, nicht für andere beschreibbare Dateien."""
    out = set()
    for directory, suffix, key, section in sources:
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for n in names:
            if not n.endswith(suffix):
                continue
            try:
                fd = os.open(os.path.join(directory, n), os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != owner or st.st_mode & 0o022:
                    continue
                text = os.read(fd, MAX_SERVICE_FILE).decode(errors="replace")
            except OSError:
                continue
            finally:
                os.close(fd)
            for cmd in _service_commands(text, key, section):
                if exe := _command_exe(cmd):
                    out.add(exe)
    return out


LOADER = re.compile(r"^ld-linux[\w.-]*\.so\.\d+$")


def loader_inspect_only(info: ProcInfo, env: dict[str, str]) -> bool:
    """Dynamischer Loader nur prüfend (``ldd``: ``--verify``/``--list`` bzw. LD_TRACE_LOADED_OBJECTS) –
    lädt das Programm, führt aber nichts davon aus."""
    return (len(info.argv) > 1 and info.argv[1] in ("--verify", "--list")) or "LD_TRACE_LOADED_OBJECTS" in env


KWALLET_INIT = "/usr/lib/pam_kwallet_init"   # kwallet-pam: übergibt beim Login das Passwort an KWallet


def kwallet_unlock(info: ProcInfo, ancestors: Iterable[ProcInfo]) -> bool:
    """``socat STDIN UNIX-CONNECT:/run/user/<uid>/kwallet{5,6}.socket``, gestartet von ``pam_kwallet_init``
    (entsperrt die Brieftasche beim Login). Nur genau dieser Aufruf – ``socat`` bleibt sonst gesperrt."""
    if not os.path.basename(info.exe).startswith("socat"):
        return False
    sockets = {f"UNIX-CONNECT:/run/user/{info.uid}/kwallet{v}.socket" for v in (5, 6)}
    if len(info.argv) != 3 or info.argv[1] != "STDIN" or info.argv[2] not in sockets:
        return False
    return any(_program_path(a) == KWALLET_INIT for a in ancestors)


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


PKG_DIR_PREFIXES = ("/usr/lib/", "/usr/libexec/", "/usr/share/", "/opt/")


def package_dirs(listing: str, exes: set[str]) -> dict[str, tuple[str, ...]]:
    """``pacman -Ql`` -> App-Programm -> eigene Ordner seines Pakets: Verzeichnisse, die keinem
    anderen Paket gehören (jeweils nur das oberste, z. B. ``/usr/lib/heroic``)."""
    owners: dict[str, set[str]] = {}
    dirs_of: dict[str, list[str]] = {}
    pkg_of: dict[str, str] = {}
    for line in listing.splitlines():
        pkg, _, path = line.partition(" ")
        if path.endswith("/"):
            owners.setdefault(path, set()).add(pkg)
            dirs_of.setdefault(pkg, []).append(path)
        elif path in exes:
            pkg_of[path] = pkg
        elif path.startswith("/usr/bin/") and (real := os.path.realpath(path)) in exes:
            pkg_of.setdefault(real, pkg)  # z. B. /usr/bin/app -> /usr/lib/app/app
    out = {}
    for exe, pkg in pkg_of.items():
        own = {d for d in dirs_of.get(pkg, ()) if owners[d] == {pkg} and d.startswith(PKG_DIR_PREFIXES)
               and d.rstrip("/") not in GENERIC_DIRS}
        tops = sorted(d.rstrip("/") for d in own if os.path.dirname(d.rstrip("/")) + "/" not in own)
        if tops:
            out[exe] = tuple(tops)
    return out


@dataclass
class Allowed:
    base: list[str] = field(default_factory=lambda: list(BUILTIN_BASE))  # Glob-Muster
    keys: set[str] = field(default_factory=set)                           # Eltern-Freigaben
    base_exact: frozenset[str] = frozenset()                              # aus der Paketkette
    excludes: list[str] = field(default_factory=lambda: list(BUILTIN_EXCLUDE))
    steam_root: str = ""   # z. B. "/srv/steam/kind/" (Steam-Bibliothek dieses Kindes)
    pkg_dirs: dict[str, tuple[str, ...]] = field(default_factory=dict)  # App -> eigene Paket-Ordner
    home: str = ""         # Home des Kindes (automatisch geladene Interpreter-Dateien)

    def __post_init__(self) -> None:
        self.exes = {k[4:] for k in self.keys if k.startswith("exe:")}
        # Steam gilt als freigegeben, wenn der Steam-Starter freigegeben ist
        self.steam_enabled = any(e.startswith(STEAM_SYSTEM) or e == "/usr/bin/steam" for e in self.exes)
        self.flatpaks = {k[8:] for k in self.keys if k.startswith("flatpak:")}
        self.app_dirs = {d for d in (os.path.dirname(e) for e in self.exes) if d not in GENERIC_DIRS}
        for e in self.exes:
            self.app_dirs.update(self.pkg_dirs.get(e, ()))
        # freigegebene Spiele-Launcher: Programm + Paket-Ordner
        self.launchers = {e for e in self.exes if e in LAUNCHERS}
        self.launcher_dirs = {d for e in self.launchers for d in self.pkg_dirs.get(e, ())}

    def path_ok(self, path: str) -> bool:
        if path in self.exes:  # ausdrückliche Eltern-Freigabe gilt immer
            return True
        if path in self.app_dirs or any(path.startswith(d + "/") for d in self.app_dirs):
            return True
        if any(fnmatch.fnmatchcase(path, pat) for pat in self.excludes):
            return False
        return path in self.base_exact or any(fnmatch.fnmatchcase(path, pat) for pat in self.base)


@dataclass(frozen=True)
class Verdict:
    key: str      # App-Schlüssel für Protokoll/Freigabe
    name: str
    detail: str = ""
    grantable: bool = True  # False: eine Freigabe von ``key`` würde nichts ändern (keine Anfrage anbieten)
    code: str = ""          # sprachneutrale Begründung (Server übersetzt), ``detail`` = deutscher Rückfall
    arg: str = ""


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


def interpreter_injection(name: str, env: dict[str, str], home: str) -> str | None:
    """Grund, falls ein Interpreter fremden Code laden würde, sonst ``None``."""
    for k in sorted(INTERP_CODE_ENV):
        if env.get(k):
            return k
    for k in sorted(INTERP_PATH_ENV):
        if k not in env:
            continue
        lua = k.startswith("LUA_")
        for entry in re.split(r"[:;]", env[k]):
            entry = entry.split("?", 1)[0]  # Lua-Muster /usr/share/lua/?.lua
            if not entry:
                if lua:
                    continue  # ";;" = Standardpfad
                return k
            if not os.path.isabs(entry) or not os.path.realpath(entry).startswith(SYSTEM_SCRIPT_DIRS):
                return k
    if home and name.startswith("python") and _python_user_site_used(home):
        return "~/.local/lib/python*/site-packages"
    return None


def _python_user_site_used(home: str) -> bool:
    """Python lädt aus ``~/.local/lib/pythonX.Y/site-packages`` automatisch ``.pth``-Dateien und
    ``usercustomize.py`` und bevorzugt dortige Module vor den System-Paketen – nicht leer = Code."""
    base = os.path.join(home, ".local", "lib")
    try:
        names = [n for n in os.listdir(base) if n.startswith("python")]
    except OSError:
        return False
    for n in names:
        try:
            if os.listdir(os.path.join(base, n, "site-packages")):
                return True
        except OSError:
            continue
    return False


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


def is_electron(exe: str) -> bool:
    return bool(ELECTRON_DIR.match(exe))


def electron_argv(info: ProcInfo) -> tuple[str, ...]:
    """Argumente eines Electron-Prozesses. Chromium überschreibt gleich beim Start
    ``/proc/<pid>/cmdline`` mit der Befehlszeile als *einem* String (setproctitle) – dann an
    Leerzeichen trennen (Pfade aus Paketen enthalten keine)."""
    if len(info.argv) == 1 and " " in info.argv[0]:
        return tuple(info.argv[0].split())
    return info.argv


def electron_app(info: ProcInfo) -> str | None:
    """App eines Electron-Hauptprozesses (erstes Nicht-Options-Argument, realpath); ``None`` bei
    Hilfsprozessen (``--type=``) oder ohne App."""
    if not is_electron(info.exe) or os.path.basename(info.exe) != "electron":
        return None
    argv = electron_argv(info)
    if any(a.startswith("--type=") for a in argv[1:]):
        return None
    app = next((a for a in argv[1:] if not a.startswith("-")), None)
    return os.path.realpath(app) if app and os.path.isabs(app) else None


def electron_args_unsafe(info: ProcInfo) -> bool:
    """Optionen, mit denen Electron fremden Code lädt oder sich fernsteuern lässt."""
    return any(a.split("=", 1)[0] in ELECTRON_UNSAFE or a.startswith(ELECTRON_UNSAFE_PREFIX)
               for a in electron_argv(info)[1:])


def _allowed_electron_main(info: ProcInfo, allowed: Allowed) -> bool:
    app = electron_app(info)
    return (app is not None and allowed.path_ok(app) and not electron_args_unsafe(info)
            and matches_host(info))


_stat = os.stat  # Tests ersetzen das


def _same(a: str, b: str) -> bool:
    try:
        x, y = _stat(a), _stat(b)
    except OSError:
        return False
    return (x.st_dev, x.st_ino) == (y.st_dev, y.st_ino)


def _decisive_paths(info: ProcInfo) -> list[str]:
    """Dateien, nach denen entschieden wird (außer dem Programm selbst): Skript bzw. Electron-App,
    so wie der Prozess sie sieht (ohne realpath im Namensraum des Agenten)."""
    name = os.path.basename(info.exe)
    out = []
    if name in SHELLS or re.sub(r"[0-9.]+$", "", name) in SHELLS:
        mode, script = shell_mode(info.argv)
        if mode == "script":
            out.append(script)
    elif INTERPRETER.match(name):
        out.append(script_arg(name, info.argv))
    elif is_electron(info.exe) and os.path.basename(info.exe) == "electron":
        argv = electron_argv(info)
        out.append(next((a for a in argv[1:] if not a.startswith("-")), None))
    return [p for p in out if p and os.path.isabs(p)]


def matches_host(info: ProcInfo) -> bool:
    """Läuft (bei einem Container-Prozess) wirklich die Systemdatei unter diesem Pfad?"""
    if not info.mnt_foreign:
        return True
    try:
        host = _stat(info.exe)
    except OSError:
        return False
    if info.exe_id != (host.st_dev, host.st_ino):
        return False
    return all(_same(f"/proc/{info.pid}/root{p}", p) for p in _decisive_paths(info))


def is_launcher_process(info: ProcInfo, allowed: Allowed) -> bool:
    """Prozess eines freigegebenen Spiele-Launchers (Starter, Electron-App oder Hilfsprogramm
    aus seinem Paket-Ordner, z. B. gogdl) – nie ein Container mit nachgebauten Dateien."""
    if not allowed.launchers or not matches_host(info):
        return False
    path = electron_app(info) or _program_path(info)
    return path in allowed.launchers or any(
        path == d or path.startswith(d + "/") for d in allowed.launcher_dirs)


def classify(info: ProcInfo, allowed: Allowed,
             ancestors: Callable[[], Iterable[ProcInfo]] | None = None,
             environ: Callable[[], dict[str, str]] | None = None) -> Verdict | None:
    """``None`` = erlaubt, sonst ``Verdict`` (was gesperrt wird). ``ancestors`` liefert die
    Elternkette, ``environ`` die Umgebung (nur für Steam/Launcher/Electron gebraucht, daher lazy)."""
    if info.flatpak_id:
        if info.flatpak_id in allowed.flatpaks:
            return None
        return Verdict(f"flatpak:{info.flatpak_id}", info.flatpak_id)

    exe = info.exe
    name = os.path.basename(exe)

    def from_steam() -> bool:
        return ancestors is not None and any(
            is_steam_process(a, allowed.steam_root) for _, a in zip(range(MAX_ANCESTORS), ancestors()))

    def chain() -> Iterable[ProcInfo]:
        return () if ancestors is None else (a for _, a in zip(range(MAX_ANCESTORS), ancestors()))

    def from_launcher() -> bool:
        return bool(allowed.launchers) and any(is_launcher_process(a, allowed) for a in chain())

    def steam_blocked() -> Verdict:
        return Verdict(STEAM_KEY, "Steam", f"nicht freigegeben – {os.path.basename(path)}",
                       code="steam_not_allowed", arg=os.path.basename(path))

    path = _program_path(info)
    if not matches_host(info):
        # Container mit eigener Datei unter diesem Pfad: nur als Laufzeit-Container von Steam/Launcher
        if from_launcher() or (allowed.steam_enabled and from_steam()):
            return None
        if from_steam():
            return steam_blocked()
        return Verdict(f"exe:{exe}", name, "Container: Datei weicht vom System ab", grantable=False, code="container")
    if allowed.steam_root and path.startswith(allowed.steam_root):
        if not allowed.steam_enabled:
            return steam_blocked()
        if is_steam_process(info, allowed.steam_root) or from_steam() or from_launcher():
            return None
        return Verdict(f"exe:{path}", os.path.basename(path), "Steam-Bibliothek (über Steam gestartet nötig)", grantable=False,
                       code="steam_library")

    if allowed.path_ok(exe):
        return None
    if from_steam():
        if allowed.steam_enabled:
            return None  # von Steam gestartet (Laufzeit-Container, Proton, …)
        return steam_blocked()  # Hilfsprogramm des Steam-Starters (z. B. cmp)
    if from_launcher():
        return None  # von Heroic/Lutris gestartet (Wine, Spiel, Hilfsprogramme)

    if LOADER.match(name) and loader_inspect_only(info, environ() if environ is not None else {}):
        return None
    if kwallet_unlock(info, chain()):
        return None

    if is_electron(exe):
        app = electron_app(info)
        if app is not None and allowed.path_ok(app):
            env = environ() if environ is not None else {}
            if electron_args_unsafe(info) or any(env.get(k) for k in ELECTRON_UNSAFE_ENV):
                return Verdict(f"exe:{app}", os.path.basename(app), f"Electron mit fremdem Code ({name})", grantable=False,
                               code="electron_injected", arg=name)
            return None
        if app is None and any(_allowed_electron_main(a, allowed) for a in chain()):
            return None  # Hilfsprozess (Renderer, GPU, Crashpad) einer erlaubten Electron-App
        if app is not None:
            return Verdict(f"exe:{app}", os.path.basename(app), f"Electron-App ({name})", code="electron_app", arg=name)
        return Verdict(f"exe:{exe}", name, "Electron ohne freigegebene App", code="electron_no_app")

    if name in SHELLS or re.sub(r"[0-9.]+$", "", name) in SHELLS:
        mode, script = shell_mode(info.argv)
        if mode == "command":
            return None
        if mode == "script":
            real = os.path.realpath(script) if os.path.isabs(script) else script
            if real.startswith(SYSTEM_SCRIPT_DIRS) or allowed.path_ok(real):
                return None
            return Verdict(f"exe:{real}", os.path.basename(real), f"Skript ({name})", code="script", arg=name)
        return Verdict(f"exe:{exe}", name, "interaktive Shell", code="interactive_shell")

    if INTERPRETER.match(name):
        script = script_arg(name, info.argv)
        if script is None:
            return Verdict(f"exe:{exe}", name, "Interpreter ohne freigegebenes Skript", code="interpreter_no_script")
        real = os.path.realpath(script) if os.path.isabs(script) else script
        if os.path.isabs(real) and allowed.path_ok(real):
            reason = interpreter_injection(name, environ() if environ is not None else {}, allowed.home)
            if reason is None:
                return None
            return Verdict(f"exe:{real}", os.path.basename(real), f"eingeschleuster Code ({reason})", grantable=False,
                           code="injected", arg=reason)
        return Verdict(f"exe:{real}", os.path.basename(real), f"Skript ({name})", code="script", arg=name)

    return Verdict(f"exe:{exe}", name)
