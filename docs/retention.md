# Data retention

Every 6 hours the server deletes what is older than:

| Data | Period | Variable |
|---|---|---|
| Log of blocked actions | 90 days | `KIDCTL_KEEP_LOG_DAYS` |
| Game sessions | 90 days | `KIDCTL_KEEP_GAMES_DAYS` |
| App usage per app | 90 days | `KIDCTL_KEEP_APPS_DAYS` |
| Screen time per day | 13 months (395 days) | `KIDCTL_KEEP_USAGE_DAYS` |
| Switch play time per day (experimental) | 13 months (395 days) | `KIDCTL_KEEP_NINTENDO_DAYS` |
| Chat | 12 months (365 days) | `KIDCTL_KEEP_CHAT_DAYS` |
| Sign-in attempts | 30 days | `KIDCTL_KEEP_LOGINS_DAYS` |
| Expired sessions and links | 30 days after expiry | `KIDCTL_KEEP_SESSIONS_DAYS`, `KIDCTL_KEEP_ADMIN_SESSIONS_DAYS`, `KIDCTL_KEEP_TOKENS_DAYS` |
| Expired pairing codes | 1 day after expiry | `KIDCTL_KEEP_PAIRING_DAYS` |
| Support access log | 2 years (730 days) | `KIDCTL_KEEP_SUPPORT_LOG_DAYS` |
| Administration log (without invoice entries) | 2 years (730 days) | `KIDCTL_KEEP_ADMIN_LOG_DAYS` |
| Closed tickets | 12 months after closing | `TICKET_RETENTION_DAYS` |
| Invoices (also after the family is deleted) | 8 years from the end of the year of issue | fixed (§ 14b UStG, § 147 AO) |
| Support access grants | 2 years after expiry | `KIDCTL_KEEP_SUPPORT_GRANTS_DAYS` |

Parents can delete a child's chat at any time under **Chat → Clear chat**. To delete a family permanently
(discard the key, then all data): `docker compose exec kidctl python -m app.cli delete-family <id>` (dry run),
then with `--apply`.
