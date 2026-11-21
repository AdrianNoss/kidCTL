# Python-Pakete für CI (mit Hashes)

`*.in` nennt die direkten Abhängigkeiten, `*.txt` ist daraus mit `pip-compile --generate-hashes` erzeugt und wird in
`workflows/tests.yml` mit `--require-hashes` installiert. Dependabot hält beide aktuell. Gleiches gilt für
`server/requirements.in` → `server/requirements.txt` (Docker-Image).

Von Hand neu erzeugen (Python 3.12 wie Image und CI), aus der Repo-Wurzel:

```sh
podman run --rm -v "$PWD":/w:Z -w /w python:3.12-slim sh -c '
  pip install -q pip-tools
  O="--quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url"
  (cd server && pip-compile $O -o requirements.txt requirements.in)
  cd .github/requirements && for f in lint tests tray; do pip-compile $O -o $f.txt $f.in; done'
```
