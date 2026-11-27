# Nintendo Switch (experimental)

KidCTL shows a Nintendo Switch's play time for the child: in the "Today" tab and in the statistics. This covers
today's play time per profile and the games played, plus the console's limit, remaining time and last sync.

> [!WARNING]
> Experimental. Nintendo offers no official interface. KidCTL uses the same cloud interface as the
> "Nintendo Switch Parental Controls" app, through the library
> [pynintendoparental](https://github.com/pantherale0/pynintendoparental). Home Assistant uses it too. If
> Nintendo changes something, the display can stop working without warning.

## What KidCTL shows and what it doesn't

- **Display only.** Switch time does not count towards the computer allowance or the time bank. You keep setting
  limits, bedtime and locks in the Nintendo app.
- **Limit and remaining time apply per console.** Nintendo has no limit per profile. KidCTL shows the play time and
  games per player profile.
- **As current as the Switch.** The values come from the Nintendo cloud. If the Switch is offline, the last state
  stays. "As of" in the "Today" tab shows when the Switch last checked in.
- KidCTL polls once a minute. The statistics keep the play time per day for 13 months, like screen time (see
  [Data retention](retention.md)).

## Setup

Requirement: the Switch is linked to your Nintendo Account in the "Nintendo Switch Parental Controls" app.

1. In KidCTL choose **Settings → Nintendo Switch → Set up connection**.
2. Open **Sign in with Nintendo**, preferably on a computer. On a phone with the Nintendo app, the app intercepts
   the next step.
3. Sign in with the Nintendo Account you use to manage the Switch in the app.
4. On the page "Linking an External Account", right-click **"Select this person"** and choose
   **"Copy link address"**. Do not click the button itself.
5. Paste the copied link into KidCTL and choose **Connect**. It starts with `npf54789befb391a838://auth`.

The sign-in link is valid for 30 minutes and only once. If it fails, choose **Generate new link** below the input field and start over.

After about a minute, Settings lists the consoles with their player profiles. Assign each profile to a child.
Profiles without an assignment (e.g. your own) stay in the list but don't appear for any child. The consoles also show up in the device overview
(profiles, today's play time, last seen).

## Security and data

- KidCTL only stores the Nintendo sign-in (session token), never your password. The token grants full access to
  your Nintendo Account. It is stored in the database, encrypted with your family's key. It is not part of the
  family export and never appears in a log.
- **Disconnect** deletes the token, the consoles and the assignments. Past play time stays in the statistics. You
  can also revoke the access in your Nintendo Account.
- After moving to another server (export/import), set up the connection again.

## If something is wrong

| Display | Meaning |
|---|---|
| "sign in again" | Nintendo rejects the sign-in (password changed, access revoked). KidCTL stops polling until you sign in again under Settings. |
| "Error" with a code | Nintendo was unreachable or reported an error. KidCTL retries automatically, with growing intervals up to 15 minutes. The last known values are shown. |
| "Error (update_required)" | Nintendo expects a newer app version. Only a KidCTL update with a newer library fixes this. |
| Console "outdated" | The last update of this console failed. The values are from the last successful update. |

## For self-hosters

| Variable | Meaning |
|---|---|
| `KIDCTL_NINTENDO` | `1`/`0`. Default: on in single-family mode, off with `KIDCTL_MODE=saas` |
| `KIDCTL_NINTENDO_INTERVAL` | Polling interval per family in seconds, minimum and default `60` |

The server needs outgoing HTTPS access to `accounts.nintendo.com`, `api.accounts.nintendo.com` and
`app.lp1.znma.srv.nintendo.net`.
