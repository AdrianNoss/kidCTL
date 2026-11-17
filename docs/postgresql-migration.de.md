# Umstieg auf PostgreSQL (einmalig)

Bis 0.32.x lag die Datenbank in `data/kidctl.db` (SQLite). Neuere Server brauchen PostgreSQL und starten mit einer
SQLite-Adresse nicht. Ablauf:

```bash
docker compose stop kidctl                         # nichts schreibt mehr in die SQLite-Datei
cp data/kidctl.db data/kidctl.db.bak            # Sicherung aufheben
git pull                                           # neue docker-compose.yml mit Datenbank-Container
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # als POSTGRES_PASSWORD in .env eintragen
docker compose build kidctl                        # neues Image (mit PostgreSQL-Treiber) – vor der Übernahme!
docker compose up -d db
docker compose run --rm kidctl python -m app.cli import-sqlite /data/kidctl.db            # Probelauf
docker compose run --rm kidctl python -m app.cli import-sqlite /data/kidctl.db --apply    # übernehmen
docker compose up -d
```

Die Übernahme arbeitet auf einer Kopie, die SQLite-Datei bleibt unverändert; sie bricht ab, wenn die
PostgreSQL-Datenbank nicht leer ist. Geräte, Sitzungen und Kopplungen bleiben gültig (gleicher `SECRET_KEY`).
Zurück: `docker compose down`, alte Version auschecken, `data/kidctl.db.bak` zurückkopieren.
