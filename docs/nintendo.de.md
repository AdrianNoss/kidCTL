# Nintendo Switch (experimentell)

KidCTL zeigt die Spielzeit einer Nintendo Switch beim Kind an: im Tab „Heute“ und in der Statistik. Das gilt für
die heutige Spielzeit je Profil und die gespielten Spiele, dazu Limit, Restzeit und Stand der Konsole.

> [!WARNING]
> Experimentell. Nintendo bietet keine offizielle Schnittstelle. KidCTL nutzt dieselbe Cloud-Schnittstelle wie die
> App „Nintendo Switch Parental Controls“, über die Bibliothek
> [pynintendoparental](https://github.com/pantherale0/pynintendoparental). Auch Home Assistant nutzt sie. Ändert
> Nintendo etwas, kann die Anzeige ohne Vorwarnung ausfallen.

## Was KidCTL anzeigt und was nicht

- **Nur Anzeige.** Die Switch-Zeit zählt nicht ins Kontingent am Computer und nicht ins Zeitkonto. Limits,
  Schlafenszeit und Sperren stellt ihr weiter in der Nintendo-App ein.
- **Limit und Restzeit gelten je Konsole.** Nintendo kennt kein Limit je Profil. Die Spielzeit und die Spiele zeigt
  KidCTL je Spielerprofil.
- **So aktuell wie die Switch.** Die Werte kommen aus der Nintendo-Cloud. Ist die Switch offline, bleibt der letzte
  Stand stehen. „Stand“ im Tab „Heute“ zeigt, wann KidCTL zuletzt neue Spielzeit
  bekommen hat. Spielt niemand, bleibt er stehen.
- KidCTL fragt einmal pro Minute ab. Die Statistik speichert die Spielzeit je Tag 13 Monate lang, wie die
  Bildschirmzeit (siehe [Aufbewahrung](retention.de.md)).

## Einrichten

Voraussetzung: Die Switch ist in der App „Nintendo Switch Parental Controls“ mit eurem Nintendo-Account verknüpft.

1. In KidCTL **Einstellungen → Nintendo Switch → Verbindung einrichten** wählen.
2. **Bei Nintendo anmelden** öffnen, am besten an einem Computer. Auf einem Handy mit der Nintendo-App fängt die App
   den nächsten Schritt ab.
3. Mit dem Nintendo-Account anmelden, mit dem ihr die Switch in der App verwaltet.
4. Auf der Seite „Verknüpfung mit einem externen Account“ mit der rechten Maustaste auf **„Diese Person auswählen“**
   klicken und **„Link-Adresse kopieren“** wählen. Den Button selbst nicht anklicken.
5. Den kopierten Link in KidCTL einfügen und **Verbinden** wählen. Er beginnt mit `npf54789befb391a838://auth`.

Der Anmeldelink gilt 30 Minuten und nur einmal. Klappt es nicht, unter dem Einfügefeld **Neuen Link erzeugen** wählen und von vorn beginnen.

Nach etwa einer Minute erscheinen unter Einstellungen die Konsolen mit ihren Spielerprofilen. Jedes Profil ordnet ihr
einem Kind zu. Profile ohne Zuordnung (z. B. eure eigenen) bleiben in der Liste, erscheinen aber bei keinem Kind.

## Sicherheit und Daten

- KidCTL speichert nur die Anmeldung bei Nintendo (Session-Token), nie euer Passwort. Das Token gewährt vollen
  Zugriff auf euren Nintendo-Account. Es liegt mit dem Schlüssel eurer Familie verschlüsselt in der Datenbank. Es
  steht nicht im Export der Familie und erscheint in keinem Protokoll.
- **Verbindung trennen** löscht das Token, die Konsolen und die Zuordnungen. Die bisherige Spielzeit bleibt in der
  Statistik. Zusätzlich könnt ihr den Zugriff in eurem Nintendo-Account widerrufen.
- Nach einem Umzug auf einen anderen Server (Export/Import) die Verbindung neu einrichten.

## Wenn etwas nicht stimmt

| Anzeige | Bedeutung |
|---|---|
| „neu anmelden“ | Nintendo lehnt die Anmeldung ab (Passwort geändert, Zugriff widerrufen). KidCTL fragt nicht mehr ab, bis ihr unter Einstellungen neu anmeldet. |
| „Fehler“ mit Code | Nintendo war nicht erreichbar oder hat einen Fehler gemeldet. KidCTL versucht es automatisch wieder, mit wachsendem Abstand bis 15 Minuten. Angezeigt werden die letzten bekannten Werte. |
| „Fehler (update_required)“ | Nintendo erwartet eine neuere App-Version. Das behebt erst ein KidCTL-Update mit neuer Bibliothek. |
| Konsole „veraltet“ | Der letzte Abruf dieser Konsole ist fehlgeschlagen. Die Werte sind vom letzten erfolgreichen Abruf. |

## Für Self-Hoster

| Variable | Bedeutung |
|---|---|
| `KIDCTL_NINTENDO` | `1`/`0`. Standard: an im Einzelbetrieb, aus mit `KIDCTL_MODE=saas` |
| `KIDCTL_NINTENDO_INTERVAL` | Abruf je Familie in Sekunden, mindestens und standardmäßig `60` |

Der Server braucht ausgehenden HTTPS-Zugriff auf `accounts.nintendo.com`, `api.accounts.nintendo.com` und
`app.lp1.znma.srv.nintendo.net`.
