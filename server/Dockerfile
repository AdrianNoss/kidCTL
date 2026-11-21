# Build-Kontext: Repo-Wurzel (wegen common/)
# Basis-Image per Digest festgelegt (Dependabot aktualisiert ihn, .github/dependabot.yml)
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/server:/srv/common KIDCTL_DATA_DIR=/data

# pg_dump/pg_restore passend zur Server-Version 17 (Backups), aus dem offiziellen PostgreSQL-Repo (PGDG)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
 && apt-get purge -y curl gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/server
COPY server/requirements.txt /srv/server/requirements.txt
# requirements.txt ist aus requirements.in erzeugt (pip-compile --generate-hashes): nur geprüfte Pakete
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt

COPY common/kidctl_common /srv/common/kidctl_common
COPY server/app /srv/server/app

RUN useradd --system --uid 10001 kidctl && mkdir -p /data && chown kidctl /data
USER kidctl

EXPOSE 8000
# Traefik setzt X-Forwarded-For. Für Sperren zählt der von Traefik angehängte letzte Eintrag – aber nur, wenn die
# Verbindung aus TRUSTED_PROXIES kommt (security.client_ip); uvicorn selbst wertet die Header nicht aus.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header"]
