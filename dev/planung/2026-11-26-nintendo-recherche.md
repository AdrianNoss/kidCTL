# Recherche: Nintendo-Switch-Spielzeit (Phase 1, nur lesen)

Stand 2026-11-26. Grundlage: Quelltext der Wheels von PyPI, HA-Integration im Zweig `dev` von home-assistant/core.

## Bibliothek

| Paket | Version | Lizenz | Abhängigkeiten |
|---|---|---|---|
| `pynintendoparental` | 2.7.0 (2026-11-18, neueste) | MIT | `pynintendoauth==1.0.3` |
| `pynintendoauth` | 1.0.3 | MIT | `aiohttp==3.*` |

- **Lizenz:** MIT ist mit AGPL-3.0 vereinbar. Eine eigene Drittanbieterliste gibt es im Repo nicht; der MIT-Hinweis liegt im installierten Paket.
- **Container:** `aiohttp` 3.14.5 ist bereits im Server-Image (über `pywebpush`). Die Bibliothek nutzt `StrEnum`
  (ab Python 3.11), das Image hat 3.12. Sie ist reines Python ohne Systembibliotheken, läuft also im Container.
- **HA** pinnt `pynintendoparental==2.6.2`. 2.7.0 bringt `discover_devices()` und `update(monthly_summary=…)`.

### API (2.7.0, verifiziert)

- `Authenticator(session_token=None, client_session=None)` erzeugt ohne Token sofort `login_url` und hält den
  PKCE-Verifier in `_auth_code_verifier` (privat, nur im Arbeitsspeicher).
- `await auth.async_complete_login(response_url)` erwartet den kopierten Link `npf54789befb391a838://auth#session_token_code=…`.
  Dann ist `auth.session_token` gesetzt, ebenso `auth.account_id`.
- `await auth.async_complete_login(use_session_token=True)` erneuert die Zugangsdaten aus dem gespeicherten Token.
- `NintendoParental(auth, timezone, lang)`, dann `await np.update(monthly_summary=False)`. `np.devices` ist ein
  Dict `device_id → Device`.
- `Device`: `device_id`, `name`, `model` („Switch“, „Switch 2“), `today_playing_time` (Minuten),
  `today_time_remaining`, `limit_time` (−1 = kein Limit), `extra_playing_time` (−1 = unbegrenzt),
  `last_sync` (Unix-Zeit oder None), `players` (je Profil `playing_time`, `apps` mit `application.name` und `playing_time`).
- **Restzeit bei „unbegrenzt“** liefert die Bibliothek *nicht* als None, sondern als Minuten bis Mitternacht. kidCTL
  muss selbst prüfen: `limit_time in (None, -1)` oder `extra_playing_time == -1` → Restzeit None.
- **Fehler:** `InvalidSessionTokenException` (400, Token ungültig/widerrufen), `InvalidOAuthConfigurationException`
  (401), `HttpException` (`status_code`, `error_code`, z. B. `update_required` = App-Version der Bibliothek veraltet),
  `NoDevicesFoundException` (404, keine Konsole verknüpft).
- `NintendoParental.update()` fängt Fehler je Konsole selbst ab und loggt sie. Für sauberes `stale` je Konsole besser
  `discover_devices()` und danach `Device.update()` einzeln aufrufen, dann kommen die Fehler beim Aufrufer an.

### Auffälligkeiten

- **Logging:** keine Tokens in den Meldungen. Auf DEBUG schreibt die Bibliothek aber komplette Tagesübersichten
  (Spielnamen, Nicknames) und URLs. Logger `pynintendoparental` und `pynintendoauth` deshalb fest auf WARNING setzen.
- **PKCE-Verifier** kommt aus `random` statt `secrets`. Das ist nicht kryptographisch stark, aber nur für den einmaligen
  Code-Austausch nötig. Der Verifier verlässt den Server nie.
- **Kein Timeout** in der Bibliothek. Eigene `aiohttp.ClientSession(timeout=…)` übergeben.
- **Last:** Ein `Device.update()` macht 3 Aufrufe (Tagesübersicht, Gerät, Einstellungen), mit Monatsübersicht
  2 mehr. Dazu kommt 1 Aufruf je Runde für die Geräteliste. Bei 60 s sind das pro Konsole etwa 240 Aufrufe je Stunde.

## Home-Assistant-Integration (`nintendo_parental_controls`)

- Config-Flow: Login-Link anzeigen, Nutzer fügt den kopierten Link ein, `async_complete_login(link)`,
  Konto-ID als eindeutige ID, gespeichert wird nur `session_token`.
- Start: `async_complete_login(use_session_token=True)`. Bei `InvalidSessionToken`/`InvalidOAuthConfiguration`
  heißt es „neu anmelden“ (Reauth-Flow mit neuem Link).
- Coordinator: 60 s, `NintendoParental.update()`. Bei `HttpException` gibt es `retry_after=900` (15 min Pause),
  bei `update_required` einen dauerhaften Fehler. Diese Werte übernimmt kidCTL als Vorbild für den Backoff.

## Bestand kidCTL (relevant)

- **Hintergrundjobs:** Modul mit `run_forever()`, gestartet in `main.lifespan` (z. B. `vacations`, `report`). Je
  Familie gilt `tenant.family_ids()` + `family_session(fid)`. Es läuft ein uvicorn-Worker, es gibt keinen Scheduler.
- **Externe Abrufe:** bisher nur Sperrlisten (`webfilter.refresh_lists`, `httpx`) und Feiertage lokal. Es gibt kein
  Provider-/Plugin-Muster. Nintendo wird ein eigenes Modul `app/nintendo.py` nach dem `run_forever`-Muster.
- **Verschlüsselung:** `Sealed("tabelle.spalte")` (AES-GCM mit dem Familienschlüssel, Kontext aus `family_session`).
  Der Server kann jederzeit entschlüsseln, weil der Familienschlüssel mit `KIDCTL_MASTER_KEY` eingepackt ist. Damit
  funktioniert Polling ohne angemeldete Eltern.
- **Nutzung:** `UsageRecord` (Sekunden je Gerät/Linux-User/Tag) wird geräteübergreifend summiert und fließt in das
  Restkontingent (`policy`), das Zeitkonto (`timebank`) und die Statistik (`stats.daily`) ein. Nintendo-Minuten dort
  hineinzuschreiben, würde die PC-Zeit des Kindes verbrauchen. Das ist für „nur lesen“ zu klären.
- **Modus:** `KIDCTL_MODE=single|saas`. Globale Schalter nur über `.env`.
