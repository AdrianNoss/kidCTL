# kidctl – Root-Agent

Läuft als root (systemd), hält Geräte-Token und Serververbindung, zählt Bildschirmzeit und setzt
die gecachte Policy auch offline durch. Nicht zugeordnete Linux-User werden nie eingeschränkt.

## Befehle

```bash
sudo kidctl enroll https://kids.nossco.de ABCD2345   # Kopplungscode aus dem WebUI
sudo kidctl run                                       # Agent (später per systemd-Unit)
sudo kidctl status                                    # Policy + heutige Nutzung je User
```

Bis zum PKGBUILD: `sudo env PYTHONPATH=<repo>/agent:<repo>/common python3 -m kidctl_agent …`

## Zustand (`/var/lib/kidctl`, root:root, 0700/0600)

| Datei | Inhalt |
|---|---|
| `device.json` | Server-URL, Geräte-ID, Token |
| `policy.json` | letzte Policy (Offline-Durchsetzung) |
| `usage.json` | lokale Zählerstände je (User, Tag), Nutzung anderer Geräte je (Kind, Tag) |

## Verhalten

- Takt 5 s: logind-Sitzungen lesen, Zeit zählen (`CLOCK_BOOTTIME`, Lücken > 30 s = Suspend,
  nicht gezählt), Entscheidung je zugeordnetem User.
- Gezählt/durchgesetzt werden nur Sitzungen der Klasse `user` (SDDM, TTY, SSH) – nicht
  Greeter, `manager` (user@.service) oder `background`.
- Zählung je User höchstens einmal pro Takt, nur wenn `Active=yes` und `LockedHint=no`.
- Vorwarnungen bei 15/5/1 min (KDE-Benachrichtigung via `notify-send`, IDs per `setpriv`, ohne
  PAM-Sitzung). Bei Zeitende 60 s Countdown, dann Abmeldung: Plasma-Sitzungen über
  `org.kde.Shutdown.logout` (nach 20 s hart, ggf. Neustart des Display-Managers, wenn seat0 leer
  ist), TTY/SSH per `TerminateSession`. Wird in der Zeit verlängert, wird abgebrochen.
- Nutzungsbericht an den Server alle 60 s und nach jedem Verbindungsaufbau.
- Reconnect mit Backoff 2 s … 5 min; Token ungültig → 1 h, Protokoll zu alt → 6 h.

## Tests

```bash
cd agent && python -m pytest -q tests   # benötigt server/requirements.txt + dbus-next, pytest
```
