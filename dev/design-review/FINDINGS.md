# Design-Durchsicht (Etappe 6) – Funde

Stand 2026-11-20, Version 0.44.0. Bilder: `dev/design-review.py --label vorher|nachher --lang de,en`
→ `dev/design-review/<label>/<lang>/<light|dark>/<seite>-<mobile|desktop>.png` (nicht im Repo, 36 Seiten × 4 Varianten je Sprache).

Erfasst: Anmeldung (beide Schritte), Registrierung, Passwort vergessen, Verwaltungs-Login (beide Schritte), alle Seiten des Elternbereichs (inkl. Support,
Einstellungen, Profil) und des Verwaltungsbereichs (Familien, Tickets, Rechnungen, Einstellungen, Codes, Protokoll,
Mehr, Erscheinungsbild, Mail-Vorschau, Newsletter, Profil). Das Skript meldet zusätzlich Konsolenfehler (u. a.
CSP-Verstöße) und waagerechtes Scrollen.

Nicht erfasst: Seiten hinter Einmal-Links (Authenticator einrichten, Einladung annehmen, Registrierung bestätigen,
neues Passwort) und der Kopplungscode – sie nutzen dieselben Komponenten wie die erfassten Anmeldeseiten bzw. stehen
in den Doku-Screenshots (`dev/screenshots.py`). Eigene Fehlerseiten gibt es nicht (Text/JSON des Frameworks).

## Behoben

| # | Seite | Fund | Art | Behebung (zentral in `style.css`) |
|---|---|---|---|---|
| 1 | Übersicht | Restzeit „0:18 h“ bzw. „18 min“ bricht neben langem Spiele-Chip auf zwei Zeilen um (mobil, DE+EN) | Umbruch | `.kid-left strong { white-space:nowrap }` |
| 2 | Kind → Heute | „Zuletzt gesperrt“: Status-Chips („gesperrt“, „nur protokolliert“) rechts abgeschnitten, Tabelle scrollt waagerecht (mobil) | Umbruch | Tabelle nutzt `.log-table`/`.log-key` wie das Protokoll (lange Namen dürfen umbrechen) |
| 3 | Statistik | Balkendiagramm mobil: letzte Tagesbeschriftung abgeschnitten, Spalten scrollen; Wert am letzten Balken ragt rechts heraus | Umbruch/Ausrichtung | schmalere Spalten unter 480 px, letzter Wert rechtsbündig mit Hintergrund in Kartenfarbe (lesbar über dem Nachbarbalken) |
| 4 | Login, Verwaltungs-Login | Beim Laden erscheint eine englische Browser-Fehlermeldung („This is an invalid domain.“) aus dem Passkey-Vorschlag im Hintergrund | Komponente | `passkey.js`: Fehler des Hintergrund-Vorschlags nur in die Konsole; der Knopf zeigt Fehler weiter an |
| 5 | Login, Verwaltungs-Login | „Weiter“ und „Mit Passkey anmelden“ kleben fast aneinander | Abstand | `.btn-block + .btn-block { margin-top:.5rem }` |
| 6 | Ticket (Verwaltung), Einladungscodes | Einzelne Option (Kästchen-Kachel) ohne Abstand zum nächsten Feld/Knopf; im Internet-Filter per Inline-Style gelöst | Abstand/Uneinheitlich | `form > label.option, .mode-on > label.option { margin-bottom:1rem }` |
| 7 | Mail-Vorschau | Filterkarte beginnt mit einer Trennlinie ohne Inhalt darüber | Ausrichtung | Trennlinie der `.filter-grid` entfällt, wenn sie erstes Element der Karte ist |
| 8 | Alle (12 Stellen in 8 Templates) | Inline-Styles (Abstände, Ausrichtung, Trennlinie, Balkenmaße) statt zentraler Klassen; CSP brauchte deshalb `style-src 'unsafe-inline'` (Issue #3) | Uneinheitlich/Sicherheit | Klassen `.section-title.first`, `.kid-used`, `.row.end`, `.row.spaced`, `td.check-col`, globales `hr`, `.card-head .segmented`; Diagramme über `p-0`…`p-100` (`web.pct`) |
| 9 | Mail-Vorschau (Verwaltung), Berichtsvorschau (Profil) | Mail-HTML mit Inline-Styles würde unter der neuen CSP ungestylt angezeigt (der `srcdoc`-Rahmen erbt die CSP der Seite) | Sicherheit | `mailer.csp_safe()`: style-Attribute → Regeln in `<style nonce>`; nur diese zwei Antworten erlauben die Nonce |

Nachher-Lauf: keine Konsolenfehler, keine CSP-Verstöße, kein waagerechtes Scrollen auf allen 36 Seiten (DE+EN, hell/dunkel, mobil/Desktop).

## Zurückgestellt (begründet)

| Seite | Fund | Grund |
|---|---|---|
| Familien (Verwaltung) | Platzhalter „Familie suchen – Name oder #ID“ mobil abgeschnitten | Feld ist beschriftet (Lupe + Knopf), Platzhalter ist nur Hilfe; kürzerer Text bräuchte neue Übersetzung |
| Kind-Reiter, Protokoll-Filter | Reiter bzw. Segmente laufen mobil rechts aus dem Bild | gewollt: waagerecht scrollbar (wie in Material 3), aktive Auswahl bleibt sichtbar |
| Protokoll | lange Schlüssel (`domain:games.example`) brechen mitten im Wort | gewollt (`overflow-wrap:anywhere`), sonst scrollt die Tabelle |
| Erscheinungsbild | Datei-Auswahl zeigt „Choose File / No file chosen“ | Browser-Steuerelement, Text kommt aus der Browsersprache |
| Profil | „Vorschau“ (Textknopf) wirkt gegenüber „Speichern“ eingerückt | M3-Textknopf hat Innenabstand; gleiches Muster überall |
| Rechnungsentwurf | mobile Tabellenzeilen zeigen „Menge:“ mit Doppelpunkt | Muster aller responsiven Tabellen (Beschriftung vor dem Wert) |
| Alle (mobil) | In den ganzseitigen Bildern steht die untere Navigation mitten im Bild | Artefakt der Ganzseiten-Aufnahme (fixierte Leiste), im Browser korrekt |
| Rechnungsentwurf (dunkel) | deaktivierter Knopf „Ausstellen und senden“ kontrastarm | gewollt (deaktiviert, M3: 38 % Deckkraft); Grund steht im Hinweis darüber |
