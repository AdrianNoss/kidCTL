# Switching to PostgreSQL (one-time)

Up to 0.32.x the database was in `data/kidctl.db` (SQLite). Newer servers need PostgreSQL and don't start with an
SQLite address. Procedure:

```bash
docker compose stop kidctl                         # nothing writes to the SQLite file any more
cp data/kidctl.db data/kidctl.db.bak               # keep a backup
git pull                                           # new docker-compose.yml with database container
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # enter as POSTGRES_PASSWORD in .env
docker compose build kidctl                        # new image (with PostgreSQL driver) – before the import!
docker compose up -d db
docker compose run --rm kidctl python -m app.cli import-sqlite /data/kidctl.db            # dry run
docker compose run --rm kidctl python -m app.cli import-sqlite /data/kidctl.db --apply    # import
docker compose up -d
```

The import works on a copy; the SQLite file stays unchanged. It aborts if the
PostgreSQL database is not empty. Devices, sessions and pairings stay valid (same `SECRET_KEY`).
Going back: `docker compose down`, check out the old version, copy `data/kidctl.db.bak` back.
