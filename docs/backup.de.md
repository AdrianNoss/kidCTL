# Backup und Wiederherstellung

Der Server legt täglich ab 3 Uhr einen geprüften Dump der Datenbank an (`pg_dump`, komprimiert):
`data/backups/kidctl-JJJJ-MM-TT-HHMM.dump`. Die letzten 14 bleiben erhalten. Zeitpunkt und Größe des
letzten Backups stehen im WebUI unter **Einstellungen**, dort gibt es auch „Jetzt sichern“. Schlägt ein
Backup fehl oder gibt es seit 2 Tagen keines, kommt eine Push-Nachricht. Per Kommandozeile:
`docker compose exec kidctl python -m app.cli backup`.

Das externe Backup des Servers sollte `./data` mitsichern, dann sind die Dumps dabei.

Wiederherstellen:

```bash
docker compose stop kidctl
docker compose exec -T db pg_restore --clean --if-exists -U kidctl -d kidctl < data/backups/kidctl-2026-11-16-0300.dump
docker compose start kidctl
```
