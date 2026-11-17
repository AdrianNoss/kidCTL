# Backup und Wiederherstellung

Der Server legt täglich ab 3 Uhr einen geprüften Dump der Datenbank an (`pg_dump`, komprimiert):
`data/backups/kidctl-JJJJ-MM-TT-HHMM.dump`. Die letzten 14 bleiben erhalten. Zeitpunkt und Größe des
letzten Backups stehen im WebUI unter **Einstellungen**, dort gibt es auch „Jetzt sichern“. Schlägt ein
Backup fehl oder gibt es seit 2 Tagen keines, kommt eine Push-Nachricht. Mit `KIDCTL_MODE=saas` sehen Eltern
diesen Bereich nicht (die Datenbank enthält alle Familien); den Stand zeigt dann der Verwaltungsbereich `/admin`,
ohne Download. Per Kommandozeile:
`docker compose exec kidctl python -m app.cli backup`.

Das externe Backup des Servers sollte `./data` mitsichern, dann sind die Dumps dabei.

Wiederherstellen:

```bash
docker compose stop kidctl
docker compose exec -T db pg_restore --clean --if-exists -U kidctl -d kidctl < data/backups/kidctl-2026-11-16-0300.dump
docker compose start kidctl
```

## Export und Import einer Familie

Unabhängig vom Datenbank-Backup kann der Inhaber unter **Einstellungen → Daten der Familie** alle Daten seiner
Familie als ZIP exportieren (mit dem aktuellen Passwort): Kinder mit Regeln, Programmen und Web-Einstellungen, Geräte
und ihre Benutzer, Nutzung, Protokoll und Chat – entschlüsselt und lesbar (JSON, mit Formatversion). Eltern-Accounts,
Passwörter und Authenticator sind nicht enthalten.

So zieht eine Familie zwischen Self-Hosting und einem gehosteten Server um:

1. Auf dem alten Server exportieren.
2. Auf dem neuen Server ein Konto anlegen (Self-Hosting: `create-parent`, gehostet: registrieren). Die Familie ist
   dann noch leer.
3. Dort unter **Einstellungen → Daten der Familie** die Datei importieren. Import geht nur in eine leere Familie.
4. Die Computer der Kinder neu koppeln: Die übernommenen Geräte erscheinen als getrennt, weil ihre Kopplung nur auf dem
   alten Server gilt. Weitere Elternteile neu einladen.

Im gehosteten Betrieb kann der Inhaber dort auch die **Familie löschen** (mit Passwort und „LÖSCHEN“): Verbundene
Agenten heben vorher alle Einschränkungen auf, dann werden der Schlüssel der Familie verworfen und alle Accounts und
Daten gelöscht.
