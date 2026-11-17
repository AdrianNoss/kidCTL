# Aufbewahrung

Der Server löscht alle 6 Stunden, was älter ist als:

| Daten | Frist | Variable |
|---|---|---|
| Protokoll gesperrter Aktionen | 90 Tage | `KIDCTL_KEEP_LOG_DAYS` |
| Spielsitzungen | 90 Tage | `KIDCTL_KEEP_GAMES_DAYS` |
| Programmnutzung je Programm | 90 Tage | `KIDCTL_KEEP_APPS_DAYS` |
| Bildschirmzeit je Tag | 13 Monate (395 Tage) | `KIDCTL_KEEP_USAGE_DAYS` |
| Chat | 12 Monate (365 Tage) | `KIDCTL_KEEP_CHAT_DAYS` |
| Anmeldeversuche | 30 Tage | `KIDCTL_KEEP_LOGINS_DAYS` |
| Abgelaufene Sitzungen und Links | 30 Tage nach Ablauf | `KIDCTL_KEEP_SESSIONS_DAYS`, `KIDCTL_KEEP_TOKENS_DAYS` |
| Abgelaufene Kopplungscodes | 1 Tag nach Ablauf | `KIDCTL_KEEP_PAIRING_DAYS` |

Den Chat eines Kindes können Eltern jederzeit unter **Chat → Chat leeren** löschen. Eine Familie endgültig löschen
(Schlüssel verwerfen, dann alle Daten): `docker compose exec kidctl python -m app.cli delete-family <id>` (Probelauf),
dann mit `--apply`.
