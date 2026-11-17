# Backup and restore

Every day from 3 am the server creates a verified dump of the database (`pg_dump`, compressed):
`data/backups/kidctl-YYYY-MM-DD-HHMM.dump`. The last 14 are kept. Time and size of the
last backup are shown in the web interface under **Settings**, which also has "Back up now". If a
backup fails or there hasn't been one for 2 days, a push notification is sent. With `KIDCTL_MODE=saas` parents
don't see this section (the database contains all families); the administration area `/admin` then shows the
status, without download. From the command line:
`docker compose exec kidctl python -m app.cli backup`.

The server's external backup should include `./data`; then the dumps are included.

Restore:

```bash
docker compose stop kidctl
docker compose exec -T db pg_restore --clean --if-exists -U kidctl -d kidctl < data/backups/kidctl-2026-11-16-0300.dump
docker compose start kidctl
```

## Export and import of a family

Independently of the database backup, the owner can export all of their family's data as a ZIP under
**Settings → Your family's data** (with the current password): children with rules, programs and web settings, devices
and their users, usage, log and chat – decrypted and readable (JSON, with a format version). Parent accounts,
passwords and authenticators are not included.

This is how a family moves between self-hosting and a hosted server:

1. Export on the old server.
2. Create an account on the new server (self-hosting: `create-parent`, hosted: sign up). The family is then still
   empty.
3. Import the file there under **Settings → Your family's data**. Import only works into an empty family.
4. Pair the children's computers again: the imported devices appear as disconnected because their pairing is only
   valid on the old server. Invite other parents again.

In hosted mode, the owner can also **delete the family** there (with password and "DELETE"): connected agents lift
all restrictions first, then the family's key is discarded and all accounts and data are deleted.
