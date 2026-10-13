# Build-Kontext: Repo-Wurzel (wegen common/)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/server:/srv/common KIDCTL_DATA_DIR=/data

WORKDIR /srv/server
COPY server/requirements.txt /srv/server/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY common/kidctl_common /srv/common/kidctl_common
COPY server/app /srv/server/app

RUN useradd --system --uid 10001 kidctl && mkdir -p /data && chown kidctl /data
USER kidctl

EXPOSE 8000
# Traefik setzt X-Forwarded-For; Container ist nur über das Traefik-Netz erreichbar.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
