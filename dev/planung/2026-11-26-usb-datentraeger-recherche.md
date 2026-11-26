# USB-Sticks und externe Datenträger einschränken – Machbarkeit

Stand 2026-11-26, Code-Basis `1d5f244` (`origin/dev`, 0.48 in Arbeit). Nur Recherche: nichts umgesetzt, nichts auf
einem Rechner scharf geschaltet. Punkte mit **(VM)** müssen vor der Umsetzung in der Test-VM bestätigt werden.

**Frage:** Kann kidCTL das Einbinden und Nutzen von USB-Sticks und externen Platten auf Kinderrechnern einschränken,
damit sich darüber keine Spiele oder Programme an Programmkontrolle und Spiele-Limit vorbei starten lassen?

**Kurzantwort:** Ja, auf beiden Plattformen je Kind und offline, ohne Tastatur, Maus, Headset oder Controller
anzufassen. Unter Linux reicht eine polkit-Regel für udisks2 (sperren), dazu ein Remount durch den Agenten (nur
lesen / `noexec`). Unter Windows gibt es die Richtlinie „Wechselmedien“ je Benutzer (`HKU\<SID>\Software\Policies`),
auch unter Home **(VM)**. Wichtig zur Einordnung: Im Modus **„Sperren“** beendet die Programmkontrolle Programme vom
Stick schon heute. Die eigentliche Lücke liegt in den Modi „Aus“ und „Nur protokollieren“. Dort ist ein Stick aber
nur *ein* Weg neben dem Download ins Home.

## Ist-Stand im Code

| | Linux | Windows |
|---|---|---|
| Einbinden | udisks2 (KDE über Solid, GNOME über gvfs) nach `/run/media/<user>/…` bzw. `/media/<user>/…`, Standardoptionen `nosuid,nodev`, **ausführbar**. kidCTL setzt keine polkit-Regeln (`packaging/` hat keine). | Normaler Laufwerksbuchstabe, keine Einschränkung durch kidCTL |
| Ausführverbot vorab | Nur Home, `/tmp`, `/var/tmp`, `/dev/shm`, Spieleordner (`kidmounts.py`, `kidctl-noexec.service`). **Kein** `noexec` auf Wechselmedien. | NTFS-ACL `Deny X` nur auf Profil, `C:\Users\Public` und `%WINDIR%\Temp` (`win/acl.py`). Auf FAT/exFAT-Sticks sind ACLs gar nicht möglich. |
| Programmkontrolle „Sperren“ | Whitelist (`programs.classify`): Ein Programm unter `/run/media/…` ist weder Basis noch freigegeben → **gesperrt** (SIGKILL nach dem `exec` per `PROC_EVENTS`). Shell-Skripte vom Stick: gesperrt (nur `/usr`, `/etc`, `/opt` erlaubt). | `win/programs.classify`: `.exe` auf `E:\` gehört nicht dem Kind (FAT: kein Besitzer, NTFS: fremde SID) → läuft durch bis „sonst gesperrt“ → **beendet** (Abfrage alle 0,5 s), Toast mit „Freigabe anfragen“. |
| „Nur protokollieren“ / „Aus“ | Läuft. (`noexec`-Home gibt es dort seit 0.45 ohnehin nicht mehr, ein Download ins Home läuft also genauso.) | Läuft. (ACL nur bei „Sperren“, Download ins Profil läuft ebenso.) |
| „Zählt nicht“ (K5) | `apptime.free_process_ok`: nur root-eigene Programmdateien. Ein FAT/exFAT-Stick gehört dem Kind → ausgeschlossen. Ein ext4-Stick mit root-eigenen Dateien bestünde die Prüfung, aber nur, wenn `AppIndex` den Prozess einem ausgenommenen Programm zuordnet (Katalogpfad) → praktisch nicht relevant. | `win/apptime.free_process_ok`: nur Store-Apps und `PROTECTED` (Programme, Windows) → Stick ausgeschlossen. |
| Spiele-Limit | Ein Spiel vom Stick ohne Launcher zählt nur als Spiel, wenn es im Katalog als „zählt als Spiel“ markiert ist. Über Steam/Heroic/Lutris gestartet: Launcher-Regeln wie bisher. | Ebenso. Steam-Bibliotheken auf externen Platten stehen in `libraryfolders.vdf` → `launcher_dirs`, also erlaubt, wenn Steam frei ist (bekannte Restlücke „Steam = alles“). |

### Befunde aus dem Code (auch ohne neues Feature relevant)

1. **Freigabe eines Programms auf dem Stick ist pfadbasiert.** Geben Eltern eine Anfrage `exe:E:\Spiele\x.exe` bzw.
   `exe:/run/media/kind/STICK/x` frei, gilt künftig *jede* Datei an diesem Pfad. Windows erlaubt zusätzlich den
   ganzen Programmordner (`WinAllowed.dirs` → `app_root`), Linux das App-Verzeichnis (`app_dirs`, außer
   `GENERIC_DIRS`). Das Kind kann Inhalt oder Stick austauschen. Vorschlag (klein, unabhängig): Programme auf
   Wechselmedien **nicht freigebbar** (`grantable=False`) oder nur mit Warnhinweis im WebUI. Bei einer neuen
   Restlücke vorher in `docs/limitations*` eintragen.
2. **„Sperren“ greift erst nach dem Start.** Weil auf dem Stick kein `noexec`/ACL liegt, startet das Programm und
   wird dann beendet. Linux: Millisekunden. Windows: bis 0,5 s, sehr kurze Programme können durchrutschen (bekannte
   Restlücke „Sehr kurze Programme“, die heute nur Systemprogramme betrifft, weil eigene Dateien vorab gesperrt
   sind). Außerdem können freigegebene Programme **Bibliotheken** (DLL/`.so`) vom Stick laden. Beides wäre eine
   Lücke, die so noch nicht in `docs/limitations*` steht.
3. **Virtuelle Sticks:** Linux `udisksctl loop-setup` bzw. GNOME Disks „Abbild einhängen“ (polkit
   `org.freedesktop.udisks2.loop-setup`, Standard für aktive Sitzungen vermutlich ohne Passwort **(VM)**) hängt eine
   Abbilddatei aus dem Home ausführbar unter `/run/media/<kind>` ein. Windows: Doppelklick auf eine `.iso` bindet
   sie als DVD-Laufwerk ein. Jede Lösung muss das mit abdecken.

## Mechanismen Linux (Arch, Debian 13, Ubuntu 24.04+, KDE und GNOME)

| Mechanismus | je Kind? | Abstufung | HID sicher? | Bewertung |
|---|---|---|---|---|
| **polkit-Regel für udisks2** (`/etc/polkit-1/rules.d/49-kidctl-media.rules`): für gelistete Kinder `polkit.Result.NO` bei `filesystem-mount`, `filesystem-mount-other-seat`, `loop-setup`, `encrypted-unlock` | ja (`subject.user`) | sperren; Ausnahmen je Gerät über `action.lookup("drive.serial")` / `id.uuid` | ja, betrifft nur Dateisysteme | **Empfohlen.** KDE und GNOME nutzen beide udisks2, polkitd lädt geänderte Regeln selbst neu, offline wirksam, Fehler in der Regel → Datei wird ignoriert (fail open, kein Aussperren). JS-Regeln gibt es auf allen Zielsystemen (polkit ≥ 121). |
| **Remount durch den Agenten** (`ro` bzw. `noexec,nosuid,nodev`), sobald ein Mount unter `/run/media/<kind>/` bzw. `/media/<kind>/` erscheint (`/proc/self/mountinfo` per `poll` beobachten oder udisks2-D-Bus-Signal) | ja (Mountpunkt bzw. `MountedBy`) | nur lesen, kein Ausführen | ja | Gut, mit kurzem Zeitfenster bis zum Remount (Linux kill-on-exec deckt das ab). Der Agent hat einen eigenen Mount-Namespace → Remount wie bei `kidmounts` über PID 1 (`nsenter -t 1 -m` oder eine systemd-Unit) **(VM)**. |
| udisks2 `mount_options.conf` / udev `UDISKS_MOUNT_OPTIONS_DEFAULTS=noexec` | nein, systemweit | `noexec`/`ro` für alle | ja | Verworfen als alleinige Lösung: trifft auch Admins (Grundregel). |
| **USBGuard** (`usbguard`, in Arch, Debian, Ubuntu) | nein, Gerät gilt für alle | Gerät erlauben/blocken nach Klasse, ID, Seriennummer | **riskant**: Verbunds-Geräte (Headset/Controller mit Speicher für Firmware) | Verworfen: nicht je Benutzer, eigener Daemon, falsche Regel = Tastatur weg. Höchstens später für „nur bestimmte Geräte“ systemweit. |
| sysfs `authorized` am Interface, `usb-storage`/`uas` sperren | nein | ganz sperren | Interface-Ebene ok, Modul-Sperre nicht | Verworfen (systemweit, beim Benutzerwechsel umschalten wäre fehleranfällig). |
| **fapolicyd** | ja (Regeln nach uid/gid) | Ausführen nach Pfad/Gerät verbieten | ja | Verworfen: Arch nur AUR, Debian/Ubuntu nicht im Standard. Fängt `exec` und `mmap` vorab ab (auch `.so`), kann aber das ganze System lahmlegen. Doppelt sich mit der Programmkontrolle. |
| Regel in `programs.classify` „Datei auf Wechselmedium“ | ja | kein Ausführen | ja | Ergänzung zum Remount: auch in „Nur protokollieren“/„Aus“ prüfen. Heute beobachtet der Watcher bei „Aus“ gar nicht (`watcher.py:96`), das wäre zu ändern. |

Wechselmedium erkennen: Gerät mit `removable=1` **oder** am USB-/Thunderbolt-Bus (`/sys/block/<dev>`-Pfad bzw.
udisks2 `Drive.Removable`/`ConnectionBus`). USB-Festplatten melden oft `removable=0`, Bus ist der verlässlichere
Merker **(VM)**. Interne zweite Platten mountet ein Kind ohnehin nicht (`filesystem-mount-system` = `auth_admin`).

## Mechanismen Windows 10/11 (Home und Pro)

| Mechanismus | je Kind? | Home? | Abstufung | Bewertung |
|---|---|---|---|---|
| **Richtlinie Wechselmedien** in `HKU\<SID>\Software\Policies\Microsoft\Windows\RemovableStorageDevices` (`Deny_All` an der Wurzel oder je Klasse: Wechseldatenträger `{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}`, CD/DVD inkl. ISO `{53f56308-…}`, WPD/Handys `{6AC27878-…}`, `{F33FDC04-…}`) mit `Deny_Read`, `Deny_Write`, `Deny_Execute` | ja (Benutzer-Richtlinie) | laut Microsoft-Tabelle nur Pro+, laut mehreren Anleitungen wirkt der Registry-Wert auch unter Home **(VM)** | sperren / nur lesen / nicht ausführen | **Empfohlen.** Das Kind kann `Software\Policies` nicht ändern. Admins bleiben unberührt. Offline wirksam. Offen **(VM)**: wirkt es ohne Neuanmeldung/Neu-Einstecken, und deckt es USB-Festplatten ab, die sich als feste Platte melden? Hive nicht geladen → beim Anmelden setzen oder `RegLoadKey` auf `NTUSER.DAT`. |
| **AppLocker** mit Deny-Regel für das Kind auf `%HOT%\*` (Hot-Plug-Medien) und `%REMOVABLE%\*` (CD/DVD) für Exe, DLL und Skripte | ja (Benutzer/Gruppe) | seit KB5024351 ohne Editionsprüfung; Home **(VM)** (Dienst `AppIDSvc`) | nicht ausführen, vorab, auch DLLs | Starke Ergänzung gegen Befund 2. Allerdings ändert sich die Semantik: Sobald eine Sammlung Regeln hat, ist alles ohne Allow-Regel gesperrt. Es braucht also „Allow Jeder *“ plus Deny, und DLL-Regeln kosten Leistung. Ein Fehler sperrt im schlimmsten Fall *alle* Benutzer, auch Admins. Erst als zweite Stufe. |
| App Control for Business (WDAC) | nein, nur geräteweit | ja | – | Verworfen: nicht je Benutzer, Fehler kann den Start verhindern. |
| `USBSTOR` `Start=4`, Geräteinstallation einschränken (DeviceInstall) | nein | DeviceInstall nur Pro | ganz sperren | Verworfen: systemweit, DeviceInstall trifft leicht HID-/Verbunds-Geräte. |
| Eigene Prüfung im Agenten: `.exe` auf Volume mit Bus USB/Removable (`GetDriveTypeW`, `IOCTL_STORAGE_QUERY_PROPERTY` → `BusTypeUsb`) | ja | ja | kein Ausführen (nach Start) | Ergänzung: in allen Modi prüfen, nicht freigebbar. Pendant zur Linux-Regel. |

Nur bestimmte Geräte erlauben: Unter Windows gibt es keinen eingebauten Weg „je Benutzer und je Gerät“. Möglich wäre
ein eigener Weg im Agenten: Kind vorne + fremdes Volume → Laufwerk für die Sitzung aushängen oder `Deny_All`
dynamisch setzen. Aufwand **L**, später.

## Grundregeln

- **Nur Kinder:** polkit-Regel listet nur zugeordnete Nicht-Admin-Kinder (gleiche Liste wie `programs.users()`).
  Windows schreibt nur in `HKU\<SID>` zugeordneter Kinder (Admin-Gruppe `S-1-5-32-544` ausgenommen wie heute).
  Kein systemweiter Mechanismus.
- **HID nie betroffen:** Alle empfohlenen Wege greifen auf Dateisystem- bzw. Speicherklassen-Ebene. Tastatur, Maus,
  Headset und Controller werden nicht berührt, auch nicht als Verbunds-Gerät (nur dessen Speicherteil).
- **Offline:** polkit-Datei bzw. Registry bleiben bestehen, Einstellung wie alle anderen in der gecachten Policy.
- **Integrität:** Regel-Datei bzw. Registry-Werte prüfen und im WebUI unter „Integrität“ melden. Bei „Aus“ entfernen
  (`unlock-all` und Deinstallation nehmen alles zurück).

## Bekannte Umgehungen / Restlücken (bei Umsetzung in `docs/limitations*` eintragen)

- **Geräte-Ausnahme fälschbar:** UUID/Label lassen sich auf einem anderen Rechner kopieren, Seriennummern seltener.
  Ausnahmen also nach Seriennummer, nicht nach UUID.
- **Netz statt Stick:** Netzwerkfreigaben (SMB/gvfs, KIO), Handy per MTP, Cloud-Ordner und Downloads ins Home sind
  nicht abgedeckt. Dagegen hilft nur „Sperren“ der Programmkontrolle. Ein Stick-Verbot ohne „Sperren“ ist also
  „Hürde“, keine Sicherheit, und das muss in der Hilfe stehen.
- **Steam/Heroic/Lutris:** Spiele in einer Launcher-Bibliothek auf externer Platte laufen über den freigegebenen
  Launcher. Andersherum kann „gesperrt/`noexec`“ legitime externe Spielebibliotheken kaputt machen → Ausnahme je
  Gerät oder Hinweis.
- **Zeitfenster:** Remount (Linux) und Agent-Prüfung (Windows) wirken erst nach dem Einhängen bzw. Start. Ohne
  AppLocker gilt für Windows weiter „Sehr kurze Programme“.
- **Physischer Zugriff** (Booten vom Stick) bleibt bei der Härtung, unverändert.

## Varianten

| | (A) Sperren | (B) A + nur lesen / kein Ausführen | (C) B + erlaubte Geräte | (D) C + AppLocker (Win) |
|---|---|---|---|---|
| Linux | polkit-Regel | + Remount `ro`/`noexec` + classify-Regel | + Serien-Ausnahmen in der polkit-Regel, Agent meldet gesehene Geräte | – |
| Windows | `Deny_All` je Kind | `Deny_Write`/`Deny_Execute` + Agent-Prüfung | dynamisch im Agenten (L) | Deny `%HOT%` vorab, auch DLLs |
| Aufwand | **S** + Policy/WebUI **S** | **M** | **M** Linux / **L** Windows, WebUI-Geräteliste **M** | **M**, hohes Testrisiko |
| Risiko | gering | gering–mittel (Remount im fremden Namespace) | mittel | **mittel–hoch** (trifft bei Fehler alle Benutzer) |
| Protokoll | neues Policy-Feld je Kind (`removable_media: allow/readonly/noexec/deny`), Protokollversion +1, alte Agents ignorieren es | gleich | + Geräteliste (Seriennr., Name) | gleich |

## Empfehlung

1. **Umgesetzt in 0.48:** Programme auf Wechselmedien in der Programmkontrolle **nicht
   freigebbar** machen bzw. im WebUI als „auf USB-Datenträger“ kennzeichnen (Befund 1). Befund 2 als Restlücke
   dokumentieren.
2. **Feature „Wechseldatenträger“ je Kind als Variante (B)** mit drei Stufen: *erlaubt* (Standard) / *ohne
   Programme* (lesen und schreiben, nichts ausführen) / *nur lesen* / *gesperrt*. Gilt auch für Abbilder (ISO,
   `loop-setup`) und Handys (WPD) und ist unabhängig vom Modus der Programmkontrolle. Linux: polkit + Remount +
   classify-Regel. Windows: Wechselmedien-Richtlinie je SID + Agent-Prüfung.
3. **(C) erlaubte Geräte per Hardware-Kennung** (entschieden, beide Plattformen, siehe unten). Dabei ist eine externe
   Spieleplatte der wichtigste Fall.
4. **(D) AppLocker nicht jetzt.** Erst wenn Befund 2 im Alltag eine Rolle spielt, dann nur nach eigener VM-Reihe.
5. Vor der Umsetzung VM-Prüfungen: Wechselmedien-Richtlinie unter Windows Home (Stick, USB-HDD, ISO, Handy, ohne
   Neuanmeldung), udisks2 `loop-setup` ohne Passwort, Remount über PID 1 aus dem Agent-Namespace, KDE und GNOME
   (Dolphin/Nautilus zeigen bei Sperre eine verständliche Meldung?).

## Entscheidungen (Adrian, 2026-11-26)

- **Befund 1 vorgezogen** (0.48): Freigaben nach Pfad gelten auf Wechseldatenträgern nicht, Programme darauf sind
  gesperrt und nicht anfragbar (`removable_media`). Store-Apps und Launcher-Bibliotheken bleiben erlaubt. Der Server
  lehnt eigene Programme unter `/run/media/`, `/media/` ab. Restlücke in `docs/limitations*` (siehe Spec).
- **Bestimmte Sticks anhand der Hardware-Kennung erlauben** (Variante C), auf beiden Plattformen gewünscht.
- **Grundsatz Zugriff (2026-11-26):** Alle USB-Massenspeicher (Sticks und Festplatten) sind für Kinder gesperrt,
  zugreifbar sind nur von den Eltern erlaubte Geräte (Hardware-Kennung). Damit gilt „Sperren“ als Standard statt
  *erlaubt*, und unter Windows muss auch das Einbinden bzw. der Zugriff auf fremde Geräte verhindert werden (Aufwand
  **L**, siehe unten), nicht nur das Ausführen. HID, Admins und nicht zugeordnete Benutzer bleiben unberührt.

### Hardware-Kennung – Stand der Recherche

- **Was:** USB-Seriennummer des Geräts (`iSerialNumber`), nicht die UUID oder das Label des Dateisystems. Die lassen
  sich mit `mkfs`/`mlabel` auf jedem Rechner nachbauen.
- **Linux:** udisks2 `Drive.Serial` (bzw. `Vendor`, `Model`). In der polkit-Regel per `action.lookup("drive.serial")`
  prüfbar, für Remount/Prüfung im Agenten über `/sys/block/<dev>/../serial` bzw. D-Bus.
- **Windows:** dieselbe Abfrage, die der Agent seit dem Fix nutzt (`IOCTL_STORAGE_QUERY_PROPERTY`), liefert ab
  `SerialNumberOffset` die Seriennummer. Alternativ die Geräte-Instanz-ID `USBSTOR\…\<Seriennummer>&0`. Beide
  Schreibweisen normalisieren (Großbuchstaben, ohne `&0`). Bei Geräten ohne Seriennummer erzeugt Windows eine
  Ersatz-ID (zweites Zeichen `&`), die nicht verwendbar ist.
- **Grenzen:** Billige Sticks haben oft keine oder dieselbe Seriennummer (z. B. `0123456789ABCDEF`). Solche Geräte
  im WebUI als „nicht eindeutig“ markieren, nicht erlaubbar oder nur mit Warnhinweis. Mit Hersteller-Werkzeugen lässt
  sich die Seriennummer mancher Sticks ändern → Restlücke („Hürde, keine Sicherheit“).
- **Ablauf:** Der Agent meldet gesehene Datenträger (Hersteller, Modell, Größe, Seriennummer, zuletzt gesehen) an
  den Server. Eltern haken sie unter „Erlaubte Datenträger“ an, die Policy enthält die Seriennummern, offline
  wirksam.
- **Aufwand:** Ausführen nur von erlaubten Sticks: **S–M** je Plattform, weil die Erkennung jetzt vorhanden ist.
  Einbinden fremder Sticks verhindern: Linux **S** (polkit), Windows **L** (keine eingebaute Ausnahme je Gerät und
  Benutzer → Agent müsste fremde Laufwerke für die Sitzung des Kindes aushängen).

## Offene Fragen an Adrian

0. ~~Hardware-Kennung~~ – entschieden (2026-11-26): fremde Geräte gesperrt, erlaubte nur für Dateien (Programme
   weiter gesperrt), Liste je Familie, Schalter je Kind (neue Kinder an, bestehende aus), mitgesperrt Abbilder,
   CD/DVD, SD-Karten, Handys (MTP).

1. ~~Stufen~~ – entschieden: gesperrt, nur erlaubte Geräte zugreifbar (Frage 0).
2. Soll der Schalter nur zusammen mit Programmkontrolle „Sperren“ angeboten werden, oder unabhängig davon (dann
   mit Hinweis, dass Downloads weiter gehen)?
3. ~~Befund 1 vorziehen~~ – entschieden, umgesetzt.
4. ~~Erlaubte Geräte (C)~~ – entschieden: ja, per Hardware-Kennung (siehe Frage 0).
5. ~~Standard~~ – entschieden: gesperrt (für bestehende Kinder siehe Frage 0).
6. Etappe: nach 0.50 (eigene Sperrlisten) als **0.51**? Agent-Release auf beiden Plattformen nötig.

## Etappe / Umfang (bei Umsetzung)

- Protokoll: Policy-Feld je Kind, Protokollversion +1 (Server bedient die alte weiter). Server: Modell + Alembic
  (Alt-Schema-Test), Schalter im Kind-Tab „Programme“, Tests.
- Agent Linux: polkit-Regel schreiben/entfernen, Mount-Beobachtung + Remount, classify-Regel, Integrität,
  `packaging/` (Regel-Verzeichnis, Deinstallation), Unittests ohne echte Mounts.
- Agent Windows: Registry je SID (auch nicht geladene Hives), Agent-Prüfung USB-Bus, Integrität, Unittests mit
  Attrappen.
- Doku DE/EN (`docs/`, `limitations*`), README, Funktionsliste, Roadmap, News (Minor), Release Notes, Screenshots.
- Smoke-Test in den VMs: Stick (FAT/exFAT/NTFS/ext4), USB-HDD, ISO/Abbild, je Stufe, Kind und Admin
  nebeneinander, offline. Tastatur/Maus/Headset/Controller bleiben nutzbar.

## Quellen

- Microsoft, Policy CSP ADMX_RemovableStorage (Werte `Deny_Execute` usw., Benutzer-Bereich, Editionen):
  https://learn.microsoft.com/windows/client-management/mdm/policy-csp-admx-removablestorage
- Winaero, Wechselmedien sperren (Registry, auch Home):
  https://winaero.com/disable-access-removable-storage-devices-windows-10
- Microsoft KB5024351, AppLocker ohne Editionsprüfung:
  https://support.microsoft.com/en-us/topic/kb5024351-removal-of-windows-edition-checks-for-applocker-e3a763c9-6a3e-4d9c-8623-0ffe69046470
- Microsoft, App Control / AppLocker Verfügbarkeit:
  https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/feature-availability
