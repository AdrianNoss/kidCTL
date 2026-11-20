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
| `KIDCTL_TRIAL_DAYS` | SaaS only: trial period of new families in days (default 30). |
| `BILLING_WEBHOOK_SECRET` | SaaS only, optional: secret for the subscription webhook `POST /api/billing/webhook` (see below). Empty = webhook off. |
| `SUPPORT_NOTIFY_EMAIL` | SaaS only, optional: shared address that gets a notice for every new ticket and every reply by a family (in addition to operators with "ticket emails"). |
| `TICKET_RETENTION_DAYS` | SaaS only: delete closed tickets after this many days (default 365). |
| `INVOICE_TAX_MODE` | Invoices: `small_business` (default, small business under § 19 UStG, no VAT) or `standard` (19 % VAT). Stored per invoice when issued. |
| `INVOICE_NUMBER_FORMAT` | Invoice number, default `RE-{YYYY}-{NNNN}` (consecutive per year, without gaps). |
| `INVOICE_DUE_DAYS` | Payment term in days (default 14). |
| `ADMIN_ALLOW_NETS` | Optional. Administration area `/admin` only from these networks, comma-separated (e.g. `192.168.1.0/24,2001:db8::/32`); from anywhere else it answers 404. Empty = from anywhere (password + authenticator are still required). |
| `TOTP_ISSUER` | Name in the authenticator app (default: the name from "Appearance", otherwise `KidCTL`) |
| `WEBAUTHN_RP_ID` | Domain that passkeys are bound to (default: host of `BASE_URL`). Only change it if passkeys should apply to a parent domain. |
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
`https://kids.example.org/admin`. It has its own accounts, separate from the parent accounts. Navigation works like
the parent interface: on a computer a bar on the left with all areas, on a phone Families, Tickets, Invoices (for
*Support*: Log) at the bottom and "More" for the rest.

```bash
docker compose exec kidctl python -m app.cli create-operator
```

Enter a user name and password, scan the QR code with the authenticator app. Password or authenticator lost: the
same command with `--reset` sets both again and ends all sessions of that account.

There are two **roles**: *Admin* (everything) and *Support* (view families, support view, tickets later – no
subscription, no invitation codes, no invoices). New accounts are *Admin*, otherwise `--role support`; change it with
`python -m app.cli set-operator-role <name> admin|support`. Under `/admin/profile` each operator enters their email
address, turns on "Receive ticket emails", changes their password, sets up a new authenticator and signs out other
sessions (changing the password or authenticator does this automatically, is logged and reported by email). Every change by an operator, the webhook or background jobs (family
name, subscription, pause, invitation codes, deletion) is listed in the **administration log** at `/admin/log`
(filterable by family, actor and kind, invisible to families, kept for 2 years).

The overview shows key figures (a click filters), a search by family number or name, under **Advanced**
filters by subscription status, plan, issue, inactivity (more than 7/30/90 days) and registration period (active
filters appear as chips below the search and can be removed one by one), and a sortable table (50 per page; filters and sorting are part of the address and can be
bookmarked). Apart from the family name and billing details, this is metadata only: number of children and devices,
oldest agent version, last activity, subscription and issues (devices offline, agent outdated, paused). Names and content are encrypted with each family's key
and cannot be read there. You also create **invitation codes** for sign-up here (single use, valid for 30 days, with
a note). With an email address, the code is emailed right away, with a link that fills it into the sign-up form.
"Resend" sends a new code and invalidates the old one. The address is deleted once the code is redeemed or has
expired. New families sign up at `/signup`: email address (and code) → confirmation link by email (24 h) →
password and authenticator → their own family, owner account, signed in.

**Subscription:** New families start with a trial (`KIDCTL_TRIAL_DAYS`), families from an invitation code with
"Plan Free" and all families from before the update on the plan "Free" (no expiry). You set the plan, status (trial,
active, canceled, past due) and "paid until" in the overview via the family number; the change takes effect
immediately. After expiry there is a 14-day grace period with notices (email to the owner, push, banner), then the
family is paused: the agents lift all restrictions, the web interface only shows the settings (subscription, export,
delete). After another 90 days without a subscription the family is deleted automatically, with warnings 30, 7 and
1 day before. None of this exists in single mode.

**Family name and billing details:** The owner enters the family name under "Settings" and the billing details in
the subscription card (name or company, address, country, email for invoices, optional VAT ID). If they are missing
although they are needed (paid plan, last 7 days of the trial), the family sees a notice and you see "Billing details
missing" in the overview. The family page shows the details and lets you change the name. Both are encrypted with a
separate key derived from the master key: readable in the administration area, not in a copy of the database alone;
they are deleted with the family.

For a payment provider there is a neutral webhook (only with `BILLING_WEBHOOK_SECRET`):

```bash
curl -X POST https://kids.example.org/api/billing/webhook -H "Authorization: Bearer $BILLING_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"family_id": 12, "status": "active", "provider": "anbieter", "customer": "K-123", "paid_until": "2027-11-30"}'
```

`status` is `active`, `canceled` or `past_due`; after the first call, `customer` is enough instead of `family_id`.

**Tickets:** Parents find their tickets in the "Support" menu (also while paused) and create new ones (subject,
category technical, subscription & invoice or other, message). In the administration area they are under "Tickets"
(default: open ones, oldest first; filterable by status, category, family and subject). Replies set the status to
"waiting" (or optionally "in progress" or "closed"); an **internal note** is only visible to support. When the family
replies, the ticket goes to "in progress", even a closed one – up to 14 days after closing. If a ticket waits for the
family for 14 days, it is closed. Notices by email or push never contain the content, only a link. Subject and
messages are encrypted with the platform key like the billing details; tickets are deleted with the family and are not
part of the family export.

**Invoices** (*Admin* only): Under "Invoices → Invoice settings" you enter the sender, tax number or VAT ID,
contact, bank details, footer and the price list (logged). On the family page, "Create invoice" creates a draft from
a template: service period from the day after "paid until", items freely editable (negative amounts for discounts).
"Issue and send" assigns the next number, freezes the billing address, amounts and tax mode, creates the PDF and sends
it to the family's billing email. Afterwards the invoice cannot be changed; corrections only via "Cancel invoice" (a
cancellation invoice with its own number and reference). "Mark as paid" sets the subscription to active and "paid
until" to the end of the service period – a pause ends immediately. Parents see their invoices under "Settings →
Subscription". The "Invoices" list shows totals per year and exports a year as CSV or a ZIP with all PDFs. Invoices
are kept when a family is deleted and are deleted automatically once the retention period has expired (8 years from
the end of the year of issue).

**Appearance** (*Admin* only): Under "Appearance" you set your own name, a logo and an app icon (PNG, JPEG, WebP, GIF
or ICO; the logo is scaled to 512 × 128, the icon to 512 × 512 pixels). This applies to the parent interface, the app
on the phone, sign-in and sign-up, push notifications and all emails to families; with a custom name, "powered by
KidCTL" is shown everywhere, in emails as a footer. The programs on the children's computers are still called KidCTL.
Set the sender name of the emails in `SMTP_FROM` (e.g. `Family Guard <noreply@example.org>`). Changes are logged and
visible everywhere within a minute. Under "Homepage" you set the link below the sign-in pages (`https://` only, link text
in German and English); empty, it points to the project page.

**Emails:** All emails share the same layout (light and dark, with a plain-text version) with the logo as an embedded
image – no external images or tracking. In the "Provider" block on the "Appearance" page you enter name, address,
contact email and links to the imprint, privacy policy and news; they appear in the footer of every email (empty fields
are left out, "Guides" points to the wiki). Under "Emails" you see every email type with sample data in German and
English and send it to the address in your profile with "Test email to me".

**Newsletter** (*Admin* only, SaaS only): Under "Newsletter" you write news (new versions, features) with a subject
and text in German, optionally in English. You format the text with Markdown: `##`/`###` for headings, `**bold**`,
`*italic*`, lists with `-` or `1.`, links `[text](https://…)` and `---` as a separator (images, HTML and code are not
included). After saving, "Preview" shows the finished email for each language. "Save and send test to me" sends both
versions to the address in your profile. "Send to N families" sends in the background to all parents with a confirmed address in families that opted
in – each in their language (without an English text: German), with a greeting and an unsubscribe link (including
"one-click unsubscribe" in the email program). Afterwards the newsletter cannot be changed and stays in the archive
with the number of families and emails; sending is recorded in the activity log. Families opt in themselves (off by
default): at sign-up or under "Settings → News by email"; they unsubscribe there or via the link in every email.
Existing families have not opted in.

> [!IMPORTANT]
> The invoice template has not been reviewed by a tax advisor yet. `python dev/sample_invoices.py` creates samples
> of both tax modes – show them to a tax advisor before the first real invoice.

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

Signing in takes two steps: first email address (or username) and password, then the authenticator code. The
second step allows 5 minutes and 5 attempts; after that, sign-in starts over. The admin area (`/admin/login`) works
the same way. With a passkey, both steps are skipped
(see [WebUI](webui.md#signing-in-and-passkeys)); operators create one under Administration → Profile.

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
