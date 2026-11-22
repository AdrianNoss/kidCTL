# Statusseite status.kidctl.de – Design (kurz)

Stand 2026-11-22, abgestimmt mit Adrian (Weg A).

## Wozu

Jeder (Familien, Self-Hoster, Adrian) sieht unter `status.kidctl.de` auf einen Blick, ob der kidCTL-Dienst gerade
läuft oder gestört ist – ohne Anmeldung. Gezeigt werden Eltern-WebUI, WebSocket für die Agents, Build-Pipelines und
GitHub-Repo.

- **Ziele:** einfache Ampel je Dienst plus Gesamtstatus, Verlauf der letzten Tage, unabhängig von srv02.
- **Nicht-Ziele (vorerst):** Alarme/Benachrichtigungen, manuell gepflegte Störungsmeldungen, Antwortzeit-Diagramme,
  Prüfung einzelner Familien oder Geräte.
- **Anti-Ziele:** Tracking, externe Ressourcen, Details über Lücken oder interne Adressen; Fehlalarme durch einzelne
  Aussetzer.

## Wege (abgewogen)

| Weg | Vorteil | Nachteil | Entscheidung |
|---|---|---|---|
| A: GitHub Actions prüft zeitgesteuert, statische Seite auf GitHub Pages | kein Server, unabhängig von srv02, kostenlos | Läufe verzögern sich oft 5–15 min; fällt GitHub aus, steht die Seite | **gewählt** |
| B: Uptime Kuma auf zweitem Rechner | minutengenau, Alarme fertig | weiterer Server, nichts Eigenes, Pipelines nur über Umwege | verworfen |
| C: Statusseite auf srv02 | einfach | fällt mit dem Dienst aus | verworfen |

## Entwurf

- **Eigenes öffentliches Repo** `AdrianNoss/kidctl-status` (je Repo nur eine Pages-Seite; `kidctl.de` belegt die des
  Hauptrepos). Enthält Prüfskript, Workflow, Seitenvorlage und die Messdaten als JSON. Die regelmäßigen Daten-Commits
  halten den Zeitplan aktiv (GitHub schaltet ihn sonst nach 60 Tagen ohne Aktivität ab).
- **Workflow** alle 10 Minuten (`schedule`) plus manuell; nur Lesezugriffe, keine Geheimnisse außer dem
  Standard-`GITHUB_TOKEN` für den Daten-Commit.
- **Prüfungen:**
  - Eltern-WebUI: `GET https://app.kidctl.de/healthz` → 200 und Login-Seite → 200.
  - WebSocket: Verbindungsaufbau zu `wss://app.kidctl.de/api/v1/ws` ohne Token; die erwartete, definierte Ablehnung
    zählt als „läuft“, Timeout/TLS-/5xx-Fehler als „down“.
  - Build-Pipelines: letzter abgeschlossener Lauf von `tests.yml`, `release.yml`, `pages.yml` auf `main` über die
    GitHub-API (rot = „gestört“, nicht „down“).
  - GitHub-Repo: Repo per API erreichbar + Komponentenstatus von githubstatus.com.
- **Bewertung:** Status „läuft“ / „gestört“ / „ausgefallen“; „ausgefallen“ erst nach zwei Fehlschlägen in Folge.
  Gesamtstatus = schlechtester Einzelstatus.
- **Seite:** statisches HTML, Deutsch/Englisch, hell/dunkel, ohne JavaScript-Pflicht, ohne externe Ressourcen, Optik
  passend zu kidctl.de. Banner mit Gesamtstatus, je Dienst eine Zeile mit Status und Balken für 30 Tage
  (Verfügbarkeit in %), Zeitpunkt der letzten Prüfung, Hinweis auf Prüfintervall und Verzögerung.
- **Verweise:** Link von kidctl.de (Fußzeile) und aus der Eltern-WebUI-Info-Seite auf die Statusseite.

## Von Adrian zu tun

- Repo `kidctl-status` anlegen (oder Claude per `gh` anlegen lassen), Pages aktivieren.
- DNS: `CNAME status → adriannoss.github.io`, danach „Enforce HTTPS“.

## Dem Bauenden überlassen

Skriptsprache (Python, nur Standardbibliothek + `websockets`), Datenformat und Aufbewahrung (z. B. 90 Tage,
verdichtet), exakter Ablehnungscode beim WebSocket, Layout-Details. Falls `/healthz` heute die Datenbank nicht prüft:
kleine Erweiterung im Hauptrepo (DB-Ping), mit Test.
