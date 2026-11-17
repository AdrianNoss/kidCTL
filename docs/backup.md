# Backup and restore

Every day from 3 am the server creates a verified dump of the database (`pg_dump`, compressed):
`data/backups/kidctl-YYYY-MM-DD-HHMM.dump`. The last 14 are kept. Time and size of the
last backup are shown in the web interface under **Settings**, which also has "Back up now". If a
backup fails or there hasn't been one for 2 days, a push notification is sent. From the command line:
`docker compose exec kidctl python -m app.cli backup`.

The server's external backup should include `./data`; then the dumps are included.

Restore:

```bash
docker compose stop kidctl
docker compose exec -T db pg_restore --clean --if-exists -U kidctl -d kidctl < data/backups/kidctl-2026-11-16-0300.dump
docker compose start kidctl
```
