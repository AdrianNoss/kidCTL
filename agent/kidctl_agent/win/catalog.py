"""Programmkatalog unter Windows: Startmenü-Verknüpfungen (für alle Benutzer und je Profil) und Store-Apps.

Gelesen über PowerShell (``WScript.Shell`` für .lnk-Ziele, ``PackageManager`` für Store-Apps mit aufgelösten
Anzeigenamen) – der Agent läuft als SYSTEM und darf das. Store-Apps zählen nur mit sichtbarem Eintrag
(``AppListEntry`` nicht ``none``); Hintergrundpakete ohne Eintrag sind wie unter Linux Teil der Basis.
Windows-Verknüpfungen haben keine Kategorien – eine kleine Schlüsselwortliste ordnet bekannte Apps den Gruppen
im WebUI zu, Programme unter ``%WINDIR%`` kommen nach „System & Einstellungen“.
"""

from __future__ import annotations

import json
import logging
import ntpath
import os
import re
import subprocess
from dataclasses import dataclass, field

from kidctl_common.protocol import AppEntry

log = logging.getLogger("kidctl.catalog")

WINDIR = os.environ.get("WINDIR", r"C:\Windows")
# Shells erscheinen wie unter Linux als „<name> (Shell)“ (Gruppe „Terminal & Shells“)
SHELLS = {"cmd.exe": "Eingabeaufforderung", "powershell.exe": "Windows PowerShell", "pwsh.exe": "PowerShell 7"}
SHELL_PATHS = (r"%WINDIR%\System32\cmd.exe", r"%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe",
               r"%ProgramFiles%\PowerShell\7\pwsh.exe")
SKIP_NAME = re.compile(r"(?i)uninstall|deinstall|entfernen|readme|liesmich|website|homepage|\b(hilfe|help)\b")
KEYWORDS = [   # (Muster im Namen bzw. Pfad, Kategorien)
    (r"firefox|chrome|edge|opera|brave|vivaldi|browser", ["Network", "WebBrowser"]),
    (r"outlook|thunderbird|mail", ["Network", "Email"]),
    (r"discord|teams|whatsapp|telegram|signal|skype|zoom", ["Network", "InstantMessaging"]),
    (r"word|excel|powerpoint|onenote|libreoffice|writer|calc\b|impress|acrobat|pdf", ["Office"]),
    (r"paint|gimp|krita|inkscape|photo|foto", ["Graphics"]),
    (r"spotify|vlc|media|music|musik|video|film|audacity", ["AudioVideo"]),
    (r"visual studio|vscode|code\b|python|git\b|scratch", ["Development"]),
    (r"steam|epic games|minecraft|roblox|battle\.net|ubisoft|ea app|gog galaxy|xbox|solitaire", ["Game"]),
    (r"anton|khan|duolingo|geogebra|lern", ["Education"]),
    (r"rechner|calculator|editor|notepad|uhr|clock|snipping|ausschneide", ["Utility"]),
]

# Mitgelieferte Store-Apps: Als SYSTEM löst Windows deren Ressourcen-Namen nicht auf (nur für Benutzer, bei denen
# das Paket registriert ist) – daher feste Namen (deutsch, englisch); sonst wird der Paketname lesbar gemacht.
# Pakete des Systems, die zur Grundausstattung gehören (immer erlaubt, nicht im Katalog): Shell-Teile (Suche, Widgets,
# „Klick und los“, Geräteübergreifendes), die Game Bar (startet bei jedem Spiel automatisch mit) und die Einstellungen
# (Hintergrund, Taskleiste, Infobereich; was das System ändert, verlangt ohnehin Administratorrechte)
BASE_PFN_PREFIXES = ("microsoftwindows.client.", "microsoft.xboxgamingoverlay_", "windows.immersivecontrolpanel_")
KNOWN_APPS = {
    "Microsoft.WindowsCalculator": ("Rechner", "Calculator"),
    "Microsoft.WindowsNotepad": ("Editor", "Notepad"),
    "Microsoft.Windows.Photos": ("Fotos", "Photos"),
    "Microsoft.WindowsCamera": ("Kamera", "Camera"),
    "Microsoft.WindowsAlarms": ("Uhr", "Clock"),
    "Microsoft.ZuneMusic": ("Medienwiedergabe", "Media Player"),
    "Microsoft.ZuneVideo": ("Filme & TV", "Movies & TV"),
    "Microsoft.MicrosoftStickyNotes": ("Kurznotizen", "Sticky Notes"),
    "Microsoft.ScreenSketch": ("Snipping Tool", "Snipping Tool"),
    "Microsoft.YourPhone": ("Smartphone-Link", "Phone Link"),
    "Microsoft.WindowsTerminal": ("Terminal", "Terminal"),
    "Microsoft.WindowsSoundRecorder": ("Audiorekorder", "Sound Recorder"),
    "Microsoft.WindowsFeedbackHub": ("Feedback-Hub", "Feedback Hub"),
    "Microsoft.GetHelp": ("Hilfe anfordern", "Get Help"),
    "Microsoft.BingWeather": ("Wetter", "Weather"),
    "Microsoft.BingNews": ("Nachrichten", "News"),
    "Microsoft.BingSearch": ("Bing-Suche", "Bing Search"),
    "Microsoft.GamingApp": ("Xbox", "Xbox"),
    "Microsoft.XboxGamingOverlay": ("Game Bar", "Game Bar"),
    "Microsoft.WindowsStore": ("Microsoft Store", "Microsoft Store"),
    "Microsoft.SecHealthUI": ("Windows-Sicherheit", "Windows Security"),
    "MicrosoftCorporationII.QuickAssist": ("Remotehilfe", "Quick Assist"),
    "Microsoft.Todos": ("Microsoft To Do", "Microsoft To Do"),
    "Microsoft.PowerAutomateDesktop": ("Power Automate", "Power Automate"),
    "Microsoft.Windows.DevHome": ("Dev Home", "Dev Home"),
    "Clipchamp.Clipchamp": ("Clipchamp", "Clipchamp"),
    "Microsoft.Paint": ("Paint", "Paint"),
    "Microsoft.MicrosoftSolitaireCollection": ("Solitaire & Casual Games", "Solitaire & Casual Games"),
    "Microsoft.OutlookForWindows": ("Outlook", "Outlook"),
    "Microsoft.MicrosoftOfficeHub": ("Microsoft 365 (Office)", "Microsoft 365 (Office)"),
    "MSTeams": ("Microsoft Teams", "Microsoft Teams"),
    "MicrosoftCorporationII.MicrosoftFamily": ("Microsoft Family Safety", "Microsoft Family Safety"),
    "Microsoft.MinecraftUWP": ("Minecraft", "Minecraft"),
    "Microsoft.MinecraftEducationEdition": ("Minecraft Education", "Minecraft Education"),
}


def readable(package: str) -> str:
    """``Microsoft.BingWeather`` -> ``Bing Weather`` (Anbieter weg, Binnenmajuskeln trennen)."""
    last = package.split(".")[-1]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", last) or package


SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sh = New-Object -ComObject WScript.Shell
$shell = New-Object -ComObject Shell.Application
Add-Type -Namespace KidCTL -Name Res -MemberDefinition @'
[DllImport("shlwapi.dll", CharSet = CharSet.Unicode)]
public static extern int SHLoadIndirectString(string src, System.Text.StringBuilder outBuf, int size, IntPtr reserved);
'@
function Resolve-Res($fullName, $pkgName, $value, $path) {
  if (-not $value -or -not $value.StartsWith('ms-resource:')) { return $value }
  $res = $value.Substring(12)
  if ($res.StartsWith('//')) { $uri = "ms-resource:$res" }
  elseif ($res.StartsWith('/')) { $uri = "ms-resource://$pkgName$res" }
  elseif ($res.Contains('/')) { $uri = "ms-resource://$pkgName/$res" }
  else { $uri = "ms-resource://$pkgName/Resources/$res" }
  # resources.pri direkt (klappt auch als SYSTEM, für das Pakete von Benutzern nicht registriert sind)
  foreach ($src in @((Join-Path $path 'resources.pri'), $fullName)) {
    $sb = New-Object System.Text.StringBuilder 512
    if ([KidCTL.Res]::SHLoadIndirectString("@{$src?$uri}", $sb, 512, [IntPtr]::Zero) -eq 0) { return $sb.ToString() } }
  return ''
}
$dirs = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs")
Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special -and $_.LocalPath } | ForEach-Object {
  $dirs += Join-Path $_.LocalPath 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs' }
$lnk = foreach ($d in $dirs) {
  if (-not (Test-Path -LiteralPath $d)) { continue }
  Get-ChildItem -LiteralPath $d -Recurse -Filter *.lnk | ForEach-Object {
    $s = $sh.CreateShortcut($_.FullName)
    $label = $_.BaseName
    try { $label = $shell.Namespace($_.DirectoryName).ParseName($_.Name).Name } catch {}
    [pscustomobject]@{ name = $label; target = $s.TargetPath; folder = $_.DirectoryName } } }
[void][Windows.Management.Deployment.PackageManager, Windows.Management.Deployment, ContentType = WindowsRuntime]
$pm = New-Object Windows.Management.Deployment.PackageManager
$seen = @{}
$appx = foreach ($p in $pm.FindPackages()) {
  if ($p.IsFramework -or $p.IsResourcePackage -or $p.IsBundle) { continue }
  $fam = $p.Id.FamilyName
  if ($seen.ContainsKey($fam)) { continue }
  $seen[$fam] = 1
  $path = $p.InstalledLocation.Path
  $entry = $false
  $name = ''
  try {
    [xml]$m = Get-Content -LiteralPath (Join-Path $path 'AppxManifest.xml') -Raw
    foreach ($a in $m.Package.Applications.Application) {
      if ($a.VisualElements -and $a.VisualElements.AppListEntry -ne 'none') {
        $entry = $true
        if (-not $name) { $name = Resolve-Res $p.Id.FullName $p.Id.Name $a.VisualElements.DisplayName $path } } }
    if (-not $name) { $name = Resolve-Res $p.Id.FullName $p.Id.Name $m.Package.Properties.DisplayName $path }
  } catch {}
  if (-not $name) { try { $name = $p.DisplayName } catch {} }
  [pscustomobject]@{ pfn = $fam; name = $name; path = $path; entry = $entry;
                     game = (Test-Path -LiteralPath (Join-Path $path 'MicrosoftGame.config')) } }
@{ lnk = @($lnk); appx = @($appx) } | ConvertTo-Json -Compress -Depth 3
"""


@dataclass
class Catalog:
    apps: list[AppEntry] = field(default_factory=list)
    exes: set[str] = field(default_factory=set)       # normcase-Pfade aller Verknüpfungsziele
    pfns: set[str] = field(default_factory=set)       # Store-Apps mit sichtbarem Eintrag
    known_pfns: set[str] = field(default_factory=set)  # alle gefundenen Pakete (ohne Frameworks)


def norm(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def expand(path: str) -> str:
    return ntpath.expandvars(path) if "%" in path else path


def categories(name: str, path: str) -> list[str]:
    low = f"{name} {ntpath.basename(path)}".lower()
    for pattern, cats in KEYWORDS:
        if re.search(pattern, low):
            return cats
    if norm(path).startswith(norm(WINDIR) + "\\"):
        return ["System"]
    return []


def parse(data: dict, exists=os.path.isfile, skip_dir: str = "", skip_profiles=()) -> Catalog:
    """``skip_dir``: KidCTL selbst; ``skip_profiles``: Profile von Administratoren (deren eigene Apps)."""
    skip = [norm(d) for d in ([skip_dir] if skip_dir else []) + list(skip_profiles)]
    cat = Catalog()
    by_key: dict[str, AppEntry] = {}
    for item in data.get("lnk") or []:
        target, name = (item.get("target") or "").strip(), (item.get("name") or "").strip()
        if not target.lower().endswith(".exe") or not name or SKIP_NAME.search(name):
            continue
        target = ntpath.normpath(expand(target))
        if not exists(target) or any(norm(target).startswith(d + "\\") for d in skip):
            continue   # fehlt, KidCTL selbst oder App eines Admins
        cat.exes.add(norm(target))
        key = "exe:" + target
        if norm(key) not in by_key:
            shell = ntpath.basename(target).lower() in SHELLS
            by_key[norm(key)] = AppEntry(key=key[:512], name=(f"{name} (Shell)" if shell else name)[:255],
                                         categories=["Shell"] if shell else categories(name, target))
    for raw, label in zip(SHELL_PATHS, SHELLS.values()):
        path = ntpath.normpath(expand(raw))
        if exists(path) and norm("exe:" + path) not in by_key:
            by_key[norm("exe:" + path)] = AppEntry(key="exe:" + path, name=f"{label} (Shell)", categories=["Shell"])
    for item in data.get("appx") or []:
        pfn = item.get("pfn") or ""
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,100}_[a-z0-9]{13}", pfn):
            continue
        cat.known_pfns.add(pfn)
        if not item.get("entry") or pfn.lower().startswith(BASE_PFN_PREFIXES):
            continue   # Hintergrundpaket bzw. Shell-Teil
        cat.pfns.add(pfn)
        package = pfn.rsplit("_", 1)[0]
        name = (item.get("name") or "").strip()
        name_en = ""
        if package in KNOWN_APPS:
            name, name_en = KNOWN_APPS[package]
        elif not name or name.startswith("ms-resource:"):
            name = readable(package)
        cats = ["Game"] if item.get("game") else categories(f"{name} {package}", item.get("path") or "")
        by_key["appx:" + pfn.lower()] = AppEntry(key="appx:" + pfn, name=name[:255], name_en=name_en[:255],
                                                 categories=cats)
    cat.apps = sorted(by_key.values(), key=lambda a: a.name.lower())
    return cat


def scan(run=subprocess.run, skip_dir: str = "", skip_profiles=()) -> Catalog:
    r = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", SCRIPT],
            capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.decode("utf-8", errors="replace").strip()
    if r.returncode != 0 or not out:
        raise OSError(f"Katalog nicht lesbar ({r.returncode}): {r.stderr.decode(errors='replace')[:300]}")
    cat = parse(json.loads(out), skip_dir=skip_dir, skip_profiles=skip_profiles)
    log.info("Katalog: %d Programme (%d Store-Apps)", len(cat.apps), len(cat.pfns))
    return cat
