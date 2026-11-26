# Feature-Inbox (tools/feature-inbox/README.md). Build-Kontext: tools/feature-inbox
# Basis-Image wie der Server (server/Dockerfile), per Digest festgelegt
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Berlin

# tzdata: Uhrzeit der Tagesübersicht in Ortszeit (TZ)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/feature-inbox
COPY requirements.txt .
# requirements.txt ist aus requirements.in erzeugt (pip-compile --generate-hashes)
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt
COPY feature_inbox ./feature_inbox

RUN useradd --system --uid 10002 inbox && mkdir -p /data/feature-inbox && chown inbox /data/feature-inbox
USER inbox

CMD ["python", "-m", "feature_inbox", "--apply", "--loop"]
