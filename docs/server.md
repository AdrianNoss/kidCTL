# Server

## Get the code and create the data folder

```bash
git clone https://github.com/AdrianNoss/kidCTL.git /srv/kidctl
cd /srv/kidctl
mkdir -p data
sudo chown 10001:10001 data     # the container runs as user 10001
```

`./data` contains the PostgreSQL database files (`data/postgres`, owned by the database container), the
daily database backups, block lists and the package repository (pacman and APT).

> [!IMPORTANT]
> **Back up this folder externally** – for the database, the backups in `data/backups` count, not the
> live files in `data/postgres`. See [Backup and restore](backup.md).

## Configuration (`.env`)

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # enter the output as SECRET_KEY
```

| Variable | Meaning |
|---|---|
| `SECRET_KEY` | Required. **Never change it**, otherwise all sessions and device tokens become invalid (pair devices again). |
| `KIDCTL_MASTER_KEY` | Required. Master key of the encryption (names, chat, log, statistics per family). Create it with `docker compose run --rm kidctl python -m app.cli master-key`. **Back it up separately** (password manager): without it the data can't be restored, not even from backups. Never change it. |
| `POSTGRES_PASSWORD` | Required. Database password, only letters, digits, `-` and `_` (e.g. `secrets.token_urlsafe(32)`). The database runs in the container `kidctl-db` and is only reachable by the server. |
| `BASE_URL` | Public address, e.g. `https://kids.example.org` |
| `KIDCTL_RELOCATE_TO` | Only when moving the server: the new address (`https://…`). Every agent from 0.33 switches to it as soon as the new server accepts its device token (same database, same `SECRET_KEY`); otherwise it stays with the old one. Set it on the **old** server until all devices have moved. |
| `KIDCTL_MODE` | `single` (default, one family) or `saas` (many families; email is required and replaces the user name) |
| `REGISTRATION` | Only with `KIDCTL_MODE=saas`: `invite` (default, sign-up only with an invitation code from the administration area), `open` (anyone) or `closed`. Requires sending email (`SMTP_*`). Always closed in single mode. |
| `ADMIN_ALLOW_NETS` | Optional. Administration area `/admin` only from these networks, comma-separated (e.g. `192.168.1.0/24,2001:db8::/32`); from anywhere else it answers 404. Empty = from anywhere (password + authenticator are still required). |
| `TOTP_ISSUER` | Name in the authenticator app (default `KidCTL`) |
| `SOURCE_URL` | "Source code" link in the footer (AGPL-3.0 §13); set it to your own fork if you make changes. Default: the KidCTL repository |
| `TZ_HOME` | Time zone for daily allowances and allowed hours (default `Europe/Berlin`) |
| `SECURITY_CONTACT` | Optional. Your own security contact (email address or https URL) for `/.well-known/security.txt`; listed before the project contact |
| `VAPID_*` | Web Push, see [Web Push](#web-push-recommended). Without keys there are no push notifications. |
| `SMTP_*` | Email for "Forgot password", security notices and the usage report (optional), see [Email](#email-and-account-recovery-recommended) |

## Traefik and start

Adjust in `docker-compose.yml`:

- `Host(\`kids.nossco.de\`)` → your own subdomain
- network name under `networks.traefik.name` and `traefik.docker.network` → the actual name
  (`docker network ls`)

```bash
docker compose up -d --build
docker compose logs -f kidctl      # "Application startup complete"
```

Test: `https://kids.example.org` shows the sign-in page. At `https://kids.example.org/.well-known/security.txt`
the server provides the security contacts (RFC 9116, see [security policy](../SECURITY.md)).

## Web Push (recommended)

Push is needed for chat messages, extension requests and alerts on the phone.

```bash
docker compose run --rm kidctl python -m app.cli vapid-keys
```

Enter the output in `.env`, set `VAPID_SUBJECT=mailto:<your address>`, then
`docker compose up -d`.

## Create the first parent account

```bash
docker compose exec kidctl python -m app.cli create-parent
```

Enter a user name (or, with `KIDCTL_MODE=saas`, the email address) and password, then scan the QR code
shown with the authenticator app. This first account is the **owner** of the family: only the owner invites
further parents in the web interface under "Parents" (invitation link, valid for 7 days; the invited person
sets their own password and authenticator) or removes them.

## Administration area (operator)

For running with many families (`KIDCTL_MODE=saas`) there is a separate administration area at
`https://kids.example.org/admin`. It has its own accounts, separate from the parent accounts:

```bash
docker compose exec kidctl python -m app.cli create-operator
```

Enter a user name and password, scan the QR code with the authenticator app. Password or authenticator lost: the
same command with `--reset` sets both again and ends all sessions of that account.

The area shows metadata only: families with the number of parents, children and devices, agent versions, last
activity and issues (devices offline, agent too old, paused). Names and content are encrypted with each family's key
and cannot be read there. You also create **invitation codes** for sign-up here (single use, valid for 30 days, with
a note). New families sign up at `/signup`: email address (and code) → confirmation link by email (24 h) →
password and authenticator → their own family, owner account, signed in.

**Support view:** The owner of a family grants access under "Settings → Support access" for 1, 24 or 72 hours; any
parent can end it at any time. While it is valid, the overview shows "View" next to the family. You then see its
pages like a parent, but read-only (no changes, no messages, no profiles), with a banner; the view ends after 2 hours,
with the grant or when you sign out. Every page view is logged and visible to the family in the settings. The session lasts 12 hours; `ADMIN_ALLOW_NETS` restricts the area to certain networks.

## Set up the phone

1. Open `https://kids.example.org` in the phone's browser and sign in.
2. "Add to home screen" or "Install app" (PWA). On iOS, push only works
   in the installed app.
3. In the app under "Notifications", enable push and press "Test".

## Email and account recovery (recommended)

In `.env` (then `docker compose up -d`):

| Variable | Meaning |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | Mail server, e.g. port 587 |
| `SMTP_SECURITY` | `starttls` (port 587), `ssl` (port 465) or `none` (local network only) |
| `SMTP_USER`, `SMTP_PASSWORD` | Credentials, leave empty without authentication |
| `SMTP_FROM` | Sender, e.g. `KidCTL <kidctl@example.org>` |

Each parent enters their email address under **Profile** (icon at the top right) and chooses the
language there (automatic by browser, Deutsch or English) for the web interface, emails and push. With SMTP a
confirmation link arrives (valid for 48 hours); only after that can the parent sign in with the address and receive
notices, reports and "Forgot password". Each address can only be used once. The user name remains valid for
signing in. The profile also lets you change the display name, password and authenticator.

| Problem | Solution |
|---|---|
| Forgot password | Sign-in page → "Forgot password?". The link is valid for 30 minutes and resets **only the password**. Signing in still requires the authenticator. |
| Authenticator lost (e.g. new phone) | Another parent: "Parents" → "Reset authenticator". The old authenticator is invalid immediately. Hand over the setup link shown (24 h) in person; it also asks for the password. |
| Only one parent account, or both lost | On the server: `docker compose exec kidctl python -m app.cli reset-totp <user>` or `reset-password <user>` |

When the password or authenticator changes, the parent receives an email notice and
their other sessions are ended.

**Usage report:** Under **Profile → Usage report by email**, each parent can enable a
weekly (Mondays) or monthly (on the 1st) report, sent from 7 am. "Preview" shows
it in the browser, "Send now" sends it immediately to try it out.
