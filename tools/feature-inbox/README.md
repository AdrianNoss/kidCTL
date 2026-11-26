# Feature-Inbox

Liest das Postfach `features@kidctl.de` und bereitet Vorschläge aus dem Formular „Feature vorschlagen“ (Mailformat
`KIDCTL-FEATURE-REQUEST v1`) als GitHub-Issues zur Prüfung auf. **Setzt nichts selbst um.** Nur für den Betreiber
von kidctl.de; Self-Hoster brauchen den Dienst nicht.

## Ablauf

1. Alle 15 Minuten (`FEATURE_INBOX_INTERVAL_MIN`) ungelesene Mails in `INBOX` per IMAP über TLS lesen. Gelesen wird
   mit `BODY.PEEK`, die Mail bleibt also ungelesen, bis sie verarbeitet ist.
2. Mails im v1-Format parsen. Andere Mails kommen nach `Unsortiert` und erscheinen nur als Zahl in der Übersicht.
3. Duplikat-Kandidaten bestimmen: Wortüberlappung mit offenen Issues mit Label `feature-request`.
4. Aufbereitung durch das Modell (`FEATURE_INBOX_MODEL`, Standard `claude-haiku-5-5`): Zusammenfassung, Kategorie,
   Aufwand S/M/L, betroffene Bereiche, Duplikate aus den Kandidaten und Rückfragen (nur bei erlaubtem Kontakt). Fällt
   das Modell aus oder steht `FEATURE_INBOX_LLM=none`, wird das Issue ohne Aufbereitung angelegt.
5. Issue mit den Labels `feature-request` und `needs-triage` anlegen. Den Originaltext übernimmt es als entschärften
   Zitatblock „Originaleingabe Nutzer“, dazu einen unsichtbaren Marker mit der Request-ID.
6. Die Mail als gelesen markieren und nach `Verarbeitet` verschieben. Die Request-ID wird gespeichert: Doppelte Mails
   und Wiederholungen nach einem Absturz legen kein zweites Issue an, auch über den Marker im Issue.
7. Einmal täglich ab `FEATURE_INBOX_REPORT_HOUR` Uhr eine Übersicht per Mail an `FEATURE_INBOX_REPORT_TO`: neue
   Issues, Duplikat-Verdacht, Anzahl Unsortiert und Fehler.

Bei einem GitHub-Fehler bleibt die Mail ungelesen in `INBOX`. Der nächste Lauf versucht es erneut.

## Freigabe

Die Issue-Automatik (Zeitplan-Task `kidctl-issues-taeglich`) holt ihre Liste nur über `dev/issues_freigegeben.py`.
Gesperrt sind:

- alle Issues mit `needs-triage`
- Issues mit `feature-request`, aber ohne `approved`

Zum Freigeben `needs-triage` entfernen und `approved` setzen. Den Nachweis liefert `dev/tests/test_issues_freigegeben.py`.

## Sicherheit

- **Mailinhalt ist reine Daten.** Das Modell hat keine Tools, seine Antwort ist per Structured Output auf ein festes
  Schema begrenzt, und der Code übernimmt nur geprüfte, gekürzte Felder. Repo, Labels, Titel und Aufbau des Issues
  legt allein der Code fest. Duplikat-Nummern gelten nur, wenn sie in der Kandidatenliste stehen. Vom Dienst gibt es
  nur zwei GitHub-Aufrufe: Issues auflisten und Issue anlegen.
- **Entschärfung im Issue:** Markdown wird escaped, damit keine Bilder, Links oder HTML entstehen. `@`-Erwähnungen und
  `#123`-Verweise werden durch ein unsichtbares Leerzeichen gebrochen.
- **Datenschutz** (öffentliches Repo): Die Kontaktadresse steht nie im Issue, nur im lokalen Zustand
  (`./data/feature-inbox/state.sqlite`, Modus 0600). Logs maskieren Adressen (`a***@example.org`).
- **Minimale Rechte:**
  - GitHub: Fine-grained Token nur für `AdrianNoss/kidCTL`, nur *Issues: Read and write*.
  - IMAP: Lesen, Flags setzen und verschieben.
  - Eigene Env-Datei (`feature-inbox.env`), damit Server und Inbox die Geheimnisse des anderen nicht sehen.
  - Container ohne Capabilities, schreibgeschützt und ohne Traefik oder Datenbank.
  - Der Test `test_prompt_injection_results_in_plain_issue_only` zeigt: Eine Mail mit eingebetteten Anweisungen und
    eine böswillige Modellantwort ergeben trotzdem genau ein normales Issue.

## Einrichtung (srv02)

```bash
cp tools/feature-inbox/feature-inbox.env.example feature-inbox.env && chmod 600 feature-inbox.env   # ausfüllen
mkdir -p data/feature-inbox && sudo chown 10002 data/feature-inbox
docker compose --profile feature-inbox run --rm feature-inbox python -m feature_inbox   # Probelauf, ändert nichts
docker compose --profile feature-inbox up -d feature-inbox
```

Ohne `--apply` läuft ein Probelauf: Er liest nur und protokolliert, was passieren würde. Der Container startet mit
`--apply --loop`.

## Tests

```bash
python -m pytest -q tools/feature-inbox/tests dev/tests/test_issues_freigegeben.py
```

IMAP, GitHub, Modell und SMTP sind gemockt; die Tests brauchen weder Netz noch das Paket `anthropic`.

## Lokales Modell (später)

Weiteres Backend in `feature_inbox/llm.py` mit derselben Methode `analyze(req, candidates) -> Analysis` ergänzen und
in `make()` eintragen (`FEATURE_INBOX_LLM=<name>`). `sanitize()` muss dabei weiter jede Antwort prüfen.
