"""„Zählt nicht“-Programme (K5, ergänzt 2026-11-25): Programme, die nie ausgenommen werden dürfen.

Browser, Terminals/Shells, Interpreter, Dateimanager, Entwicklungswerkzeuge und Spiele-Launcher – alles, womit sich
beliebige Inhalte oder Programme öffnen lassen. Der Server lehnt die Markierung ab, der Agent prüft sie nochmals
(eine manipulierte oder veraltete Policy kann so keinen Browser ausnehmen).
"""

from __future__ import annotations

import re

from .protocol import is_steam_key

# freedesktop-Kategorien (Windows: vom Agenten geschätzt, siehe win.catalog)
CATEGORIES = frozenset({"WebBrowser", "TerminalEmulator", "Shell", "FileManager", "Development", "PackageManager"})

# Browser: Basisname ohne „.exe“, klein. Laufen sie (auch ohne Katalog-Eintrag), zählt die Zeit immer.
BROWSERS = frozenset({
    "firefox", "firefox-esr", "firefox-bin", "firefox-developer-edition", "librewolf", "waterfox", "floorp", "zen",
    "zen-bin", "chromium", "chromium-browser", "chrome", "google-chrome", "google-chrome-stable",
    "google-chrome-beta", "brave", "brave-browser", "opera", "opera_gx", "vivaldi", "vivaldi-bin", "microsoft-edge",
    "microsoft-edge-stable", "msedge", "epiphany", "falkon", "konqueror", "midori", "qutebrowser", "torbrowser",
    "tor-browser", "start-tor-browser", "seamonkey", "palemoon", "iexplore", "thorium", "thorium-browser",
    "ungoogled-chromium", "min", "badwolf", "surf", "luakit", "nyxt", "ladybird", "mullvad-browser",
})
OTHER = frozenset({
    # Terminals und Shells
    "konsole", "yakuake", "gnome-terminal", "gnome-terminal-server", "kgx", "ptyxis", "xterm", "uxterm", "alacritty",
    "kitty", "foot", "footclient", "wezterm", "wezterm-gui", "tilix", "terminator", "xfce4-terminal", "lxterminal",
    "qterminal", "st", "urxvt", "rxvt", "terminology", "cool-retro-term", "blackbox", "ghostty", "sh", "bash", "zsh",
    "fish", "dash", "ksh", "mksh", "tcsh", "csh", "nu", "xonsh", "elvish",
    "cmd", "powershell", "powershell_ise", "pwsh", "wt", "windowsterminal", "openconsole", "conhost", "mshta",
    "wscript", "cscript", "regedit", "mmc", "rundll32", "msiexec", "wsl", "wslhost",
    # Dateimanager
    "dolphin", "nautilus", "thunar", "nemo", "pcmanfm", "pcmanfm-qt", "krusader", "doublecmd", "caja", "spacefm",
    "explorer",
    # Spiele-Launcher
    "steam", "heroic", "lutris", "bottles", "epicgameslauncher", "battle.net", "battle.net launcher", "eadesktop",
    "ealauncher", "upc", "ubisoftconnect", "galaxyclient", "itch", "playnite.desktopapp", "playnite.fullscreenapp",
    "xboxapp", "gamebar",
})
# Interpreter und Laufzeiten (mit Versionsnummern, z. B. python3.13, wine64)
INTERPRETERS = re.compile(r"^(python|pypy|perl|ruby|node|nodejs|deno|bun|java|javaw|lua|luajit|php|tclsh|wish|"
                          r"wine|wine64|wine-preloader|wine64-preloader|proton|osascript|guile|racket|R|Rscript)"
                          r"[0-9.]*$", re.IGNORECASE)

FLATPAKS = frozenset(s.lower() for s in {
    "org.mozilla.firefox", "org.mozilla.Firefox", "org.chromium.Chromium", "com.google.Chrome", "com.google.ChromeDev",
    "com.brave.Browser", "com.microsoft.Edge", "com.opera.Opera", "com.vivaldi.Vivaldi",
    "io.gitlab.librewolf-community", "net.waterfox.waterfox", "one.ablaze.floorp", "app.zen_browser.zen",
    "org.torproject.torbrowser-launcher", "com.github.micahflee.torbrowser-launcher", "org.gnome.Epiphany",
    "org.kde.falkon", "org.kde.konqueror", "io.github.ungoogled_software.ungoogled_chromium", "net.mullvad.MullvadBrowser",
    "org.kde.konsole", "org.gnome.Console", "org.gnome.Terminal", "com.raggesilver.BlackBox", "app.devsuite.Ptyxis",
    "org.kde.dolphin", "org.gnome.Nautilus", "com.valvesoftware.Steam", "com.heroicgameslauncher.hgl",
    "net.lutris.Lutris", "com.usebottles.bottles", "io.itch.itch", "com.visualstudio.code", "com.vscodium.codium",
})
# Store-Apps (Package Family Name, Anfang)
PFN_PREFIXES = ("microsoft.microsoftedge", "microsoft.windowsterminal", "microsoft.gamingapp_", "microsoft.xboxapp_",
                "microsoft.xboxgamingoverlay_", "microsoft.4297127d64ec6_", "microsoft.powershell",
                "microsoft.desktopappinstaller")


def program_name(path: str) -> str:
    """Basisname einer Programmdatei ohne „.exe“, klein (Linux- und Windows-Pfade)."""
    base = re.split(r"[/\\]", path)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def name_never_free(name: str) -> bool:
    """Basisname (siehe ``program_name``) eines Browsers, Terminals, Interpreters, Dateimanagers oder Launchers?"""
    return name in BROWSERS or name in OTHER or bool(INTERPRETERS.match(name))


def is_browser_name(name: str) -> bool:
    return name in BROWSERS


def never_free(key: str, categories=()) -> bool:
    """Darf ``key`` (App-Schlüssel) nie als „zählt nicht“ gelten?"""
    if set(categories) & CATEGORIES or is_steam_key(key):
        return True
    kind, _sep, rest = key.partition(":")
    if kind == "exe":
        return name_never_free(program_name(rest))
    if kind == "flatpak":
        return rest.lower() in FLATPAKS
    if kind == "snap":
        return name_never_free(rest.lower())
    if kind == "appx":
        return rest.lower().startswith(PFN_PREFIXES)
    return True   # unbekannte Art (z. B. erkannte Spiele „game:“)
