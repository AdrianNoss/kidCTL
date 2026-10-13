# kidctl – Tray-App

PySide6, Autostart im Kinder-Account (`/etc/xdg/autostart/kidctl-tray.desktop`). Rein kosmetisch:
Beenden ändert nichts an Zählung und Durchsetzung. Für nicht zugeordnete User bleibt das Symbol
unsichtbar.

- Symbol mit Restminuten (grün; ≤ 15 min orange; ≤ 5 min/gesperrt rot; „…“ ohne Agent).
- Menü: Restzeit/Zeitfenster, „Chat öffnen“, „Verlängerung anfordern …“. Linksklick öffnet den Chat.
- Chat mit den Eltern; Verlängerungsanfragen mit Status (wartet/genehmigt/abgelehnt/verfallen).
- Anfrage-Dialog: Minuten und/oder späteres Ende, Begründung Pflicht. Öffnet sich auch über den
  Knopf „Verlängerung anfordern“ in den Warnungen des Agenten.

Spricht nur mit dem Agenten über `/run/kidctl/tray.sock` (Protokoll `common/kidctl_common/tray.py`);
der Agent erkennt den User per `SO_PEERCRED`. Offline puffert der Agent Nachrichten/Anfragen.

Tests: `python -m pytest tests` (GUI-Test braucht PySide6, z. B. in der Test-VM mit
`QT_QPA_PLATFORM=offscreen`).
