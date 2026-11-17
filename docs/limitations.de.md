# Grenzen und bekannte Lücken

KidCTL macht es Kindern schwer, Regeln zu umgehen – vollständige Sicherheit gibt es aber nicht. Diese Seite nennt
offen, was KidCTL bewusst nicht kann und wo bekannte Lücken sind. Sie ist die vollständige Liste; die Kurzfassung
steht im README.

> [!NOTE]
> Was hier steht, gilt nicht als Sicherheitslücke im Sinne der Sicherheitsrichtlinie. Neue
> Wege, wie ein Kind ohne Admin-Rechte Zeitlimit, Programm- oder Website-Filter umgeht, bitte vertraulich melden.

## Überall

- **Physischer Zugriff:** Ohne [Härtung](hardening.de.md) lässt sich jede Sperre umgehen, z. B. mit einem
  USB-Stick oder über das Bootmenü. KidCTL selbst schützt nur das laufende System.
- **Keine Leerlauferkennung:** Die Bildschirmzeit läuft weiter, solange das Kind angemeldet ist – auch wenn es
  gerade nichts tut.
- **VPN und feste IP-Adressen:** Der Website-Filter arbeitet über DNS (Namensauflösung). Wer eine Website direkt
  über ihre IP-Adresse aufruft (auch DNS-over-HTTPS über eine IP-Adresse) oder ein VPN mit fest eingetragenen
  IP-Adressen nutzt, umgeht ihn. VPNs werden nicht aktiv erkannt.
- **Steam:** Wer Steam freigibt, gibt faktisch alles frei, was über Steam gestartet werden kann – auch über
  „Nicht-Steam-Spiel hinzufügen“. Die Steam-Bibliothek gehört dem Kind (Steam aktualisiert sich selbst), Steam ist
  nur als Ganzes steuerbar. Bei Steam und Heroic gibt es keine Steuerung einzelner Spiele.
- **Blacklist-Ausnahmen:** Eine Ausnahme gilt nicht für genauere Einträge einer Sperrliste (Beispiel: Ausnahme
  `example.com`, die Liste sperrt `ads.example.com` – das bleibt gesperrt).
- **Chat:** keine Bilder oder Dateien, keine Lesebestätigungen.
- **Kein Browserverlauf:** KidCTL speichert bewusst nicht, welche erlaubten Websites besucht wurden – nur, was
  gesperrt wurde.

## Linux

- **Desktops:** nur KDE Plasma und GNOME unter Wayland; kein X11, keine anderen Desktops (Cinnamon und COSMIC
  sollen folgen).
- **Debian/Ubuntu:** nur 64-Bit-PCs (amd64).
- **GNOME:** keine automatische Anmeldung (GDM-Autologin) für Kinder – bei aktiver Login-Sperre bliebe der
  Bildschirm schwarz. Das WebUI meldet eine solche Einstellung unter „Integrität“.
- **Ubuntu:** „Dateien“ (Nautilus) ist immer erlaubt, weil Ubuntus Desktop-Symbole es brauchen.
- **Snap und Flatpak:** Wer eine Snap- oder Flatpak-App freigibt, gibt alles in deren Sandbox frei (auch
  `snap run --shell` bzw. `flatpak run --command`). Flatpak-Apps, die das Kind nur für sich installiert
  (`flatpak --user`), werden nicht unterstützt.
- **Updates:** Der Agent aktualisiert sich unter Linux nicht selbst, sondern mit den normalen Systemupdates
  (Pacman bzw. apt).
- **Widgets und Erweiterungen:** Plasma-Widgets und GNOME-Erweiterungen, die das Kind selbst installiert, laufen
  in der Desktop-Oberfläche und sind keine Programmstarts. Zeitlimit, Website-Filter und Abmeldung umgehen sie
  nicht.
- **Shell-Befehle:** Befehle wie `sh -c …` (z. B. über KRunner) sind erlaubt, weil Teile des Systems sie brauchen.
  Starten lassen sich damit aber nur erlaubte Programme: Eigene Programme des Kindes und Skripte aus seinem
  Home-Verzeichnis laufen nicht.
- **System-Skripte:** Shell-Skripte aus `/usr`, `/etc` und `/opt` sind immer erlaubt (geprüft wird das Programm,
  das sie starten). Anwendungen, die nur aus einem Shell-Skript unter `/usr` bestehen, lassen sich daher nicht
  sperren.
- **Hilfsprogramme:** Programme, die eine freigegebene App intern aufruft (z. B. `git` aus Kate), sind gesperrt,
  bis sie selbst freigegeben werden.
- **Website-Filter:** Programme, die die Namensauflösung von systemd direkt fragen (z. B. `resolvectl`), umgehen
  ihn; Browser und normale Programme nicht. Wird die Firewall-Konfiguration neu geladen oder entfernt, kann der
  Filter bis zu 60 Sekunden offen sein, bis KidCTL ihn wiederherstellt.

## Windows

- **Nur x64:** kein Windows auf ARM.
- **Anmeldebildschirm:** Bei der Login-Sperre deaktiviert KidCTL das Konto. Windows blendet es dann auf dem
  Anmeldebildschirm aus (über „Anderer Benutzer“ erscheint nur „Konto deaktiviert“); einen eigenen Hinweis gibt es
  nicht.
- **Website-Filter für den ganzen Rechner:** Es gilt der Filter des Kindes, das gerade vorne angemeldet ist – auch
  für Dienste, Hintergrundprogramme und im Hintergrund angemeldete Benutzer, also z. B. für ein Elternteil, das
  per „Benutzer wechseln“ angemeldet bleibt, und für dessen Firmen-VPN.
- **Windows-Dienste sind immer erlaubt:** Im Whitelist-Modus bleiben Windows Update, Defender, SmartScreen,
  Zeitabgleich, Zertifikate, Lizenzen und der Verbindungstest erreichbar. Bekanntes Hintergrundrauschen
  (Telemetrie, Widgets, Wetter, Edge-Konfiguration) bleibt gesperrt und erscheint nicht im Protokoll. Bing ist die
  Standardsuche in Edge und bleibt sichtbar, damit das Kind eine Freigabe anfragen kann.
- **Laufende Verbindungen:** Die Sperre nach einem Benutzerwechsel gilt nur für neue Verbindungen; ein laufender
  Download eines Kindes läuft weiter, bis er endet. Nach dem Hinzufügen eines Netzwerkadapters (z. B. VPN) kann
  Windows bis zu 30 Sekunden dessen DNS-Server nutzen.
- **Fremder DNS-Dienst:** Beim Start beendet KidCTL jeden laufenden `unbound.exe`, auch einen, der nicht zu KidCTL
  gehört.
- **Sehr kurze Programme:** Die Programmkontrolle prüft laufend die gestarteten Programme. Sehr kurzlebige Programme
  (z. B. `reg.exe` aus `cmd /c`) können durchrutschen; eigene Programme des Kindes laufen wegen des Ausführverbots
  trotzdem nicht. `cmd /c …` ist erlaubt, weil Windows-Komponenten es brauchen – was es startet, wird geprüft.
- **Freigegebene Apps im Profil:** In den Ordner einer freigegebenen App im Benutzerprofil kann das Kind eigene
  Bibliotheken (DLLs) legen – wie bei Steam.
- **Selbst installieren:** Mit aktiver Programmkontrolle kann das Kind nichts selbst installieren (Installer im
  Download-Ordner). Programme, die Windows schon beim Start blockiert, zeigen keine Meldung und kein „Freigabe
  anfragen“ – die Eltern geben sie im WebUI frei bzw. stellen zum Installieren kurz auf „Aus“.
- **Andere beschreibbare Ordner** (z. B. unter `C:\ProgramData`) haben kein Ausführverbot: Programme darin, die
  dem Kind gehören, sind gesperrt, Bibliotheken (DLLs) daraus nicht.
- **Store-Apps:** Die mitgelieferten Microsoft-Apps zeigen Namen aus einer festen Liste; andere Store-Apps den
  Namen aus dem App-Paket.
- **Vollbildspiele:** Vorwarn-Fenster erscheinen nicht über Spielen im exklusiven Vollbild; die Abmeldung erfolgt
  trotzdem.
- **Tray-App:** Ein Kind könnte die Anzeige der Tray-App anderer Benutzer verfälschen. Die Durchsetzung durch den
  KidCTL-Dienst ist davon nicht betroffen.
- **Ohne Code-Signatur:** SmartScreen fragt beim ersten Installieren nach, Defender kann Fehlalarme melden – siehe
  [Code-Signatur](code-signing.de.md).
