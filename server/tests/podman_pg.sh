#!/usr/bin/env bash
# Für Tests: pg_dump/pg_restore im Entwicklungs-Container kidctl-pg ausführen (auf dem Rechner ist kein PostgreSQL
# installiert). Verbindung über den Unix-Socket im Container; Benutzer, Passwort und Datenbank aus der Umgebung.
tool="$1"; shift
exec podman exec -i -e PGUSER="$PGUSER" -e PGPASSWORD="$PGPASSWORD" -e PGDATABASE="$PGDATABASE" \
  "${KIDCTL_PG_CONTAINER:-kidctl-pg}" "$tool" "$@"
