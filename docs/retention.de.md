# Aufbewahrung

Der Server löscht alle 6 Stunden, was älter ist als:

| Daten | Frist | Variable |
|---|---|---|
| Protokoll gesperrter Aktionen | 90 Tage | `KIDCTL_KEEP_LOG_DAYS` |
| Spielsitzungen | 90 Tage | `KIDCTL_KEEP_GAMES_DAYS` |
| Programmnutzung je Programm | 90 Tage | `KIDCTL_KEEP_APPS_DAYS` |
| Bildschirmzeit je Tag | 13 Monate (395 Tage) | `KIDCTL_KEEP_USAGE_DAYS` |
| Switch-Spielzeit je Tag (experimentell) | 13 Monate (395 Tage) | `KIDCTL_KEEP_NINTENDO_DAYS` |
| Chat | 12 Monate (365 Tage) | `KIDCTL_KEEP_CHAT_DAYS` |
| Anmeldeversuche | 30 Tage | `KIDCTL_KEEP_LOGINS_DAYS` |
| Abgelaufene Sitzungen und Links | 30 Tage nach Ablauf | `KIDCTL_KEEP_SESSIONS_DAYS`, `KIDCTL_KEEP_ADMIN_SESSIONS_DAYS`, `KIDCTL_KEEP_TOKENS_DAYS` |
| Abgelaufene Kopplungscodes | 1 Tag nach Ablauf | `KIDCTL_KEEP_PAIRING_DAYS` |
| Protokoll der Support-Zugriffe | 2 Jahre (730 Tage) | `KIDCTL_KEEP_SUPPORT_LOG_DAYS` |
| Verwaltungsprotokoll (ohne Rechnungs-Einträge) | 2 Jahre (730 Tage) | `KIDCTL_KEEP_ADMIN_LOG_DAYS` |
| Geschlossene Tickets | 12 Monate nach Schließen | `TICKET_RETENTION_DAYS` |
| Rechnungen (auch nach Löschen der Familie) | 8 Jahre ab Ende des Ausstellungsjahres | fest (§ 14b UStG, § 147 AO) |
| Support-Freigaben | 2 Jahre nach Ablauf | `KIDCTL_KEEP_SUPPORT_GRANTS_DAYS` |

Den Chat eines Kindes können Eltern jederzeit unter **Chat → Chat leeren** löschen. Eine Familie endgültig löschen
(Schlüssel verwerfen, dann alle Daten): `docker compose exec kidctl python -m app.cli delete-family <id>` (Probelauf),
dann mit `--apply`.
