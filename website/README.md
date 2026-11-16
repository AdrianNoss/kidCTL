# Projektseite (Hugo)

Quelle der Seite <https://adriannoss.github.io/kidCTL/> (später Produkt-Domain). Hugo ohne Extended, eigenes Theme
`themes/kidctl`, kein Node. Veröffentlicht wird über `.github/workflows/pages.yml`.

```bash
cd website
gh api "repos/AdrianNoss/kidCTL/releases?per_page=100" > data/releases.json   # optional, sonst leere Release-Liste
hugo server                     # http://localhost:1313/
hugo --minify --panicOnWarning  # vor dem Commit
```

| Pfad | Inhalt |
|---|---|
| `hugo.toml` | Konfiguration; Domain-Wechsel: `baseURL` + `static/CNAME` |
| `i18n/{en,de}.yaml` | Texte der Oberfläche |
| `data/features.yaml`, `platforms.yaml`, `trust.yaml`, `install.yaml` | Inhalte der Startseite (Felder `de`/`en`) |
| `data/roadmap.yaml` | Roadmap (`planned` / `in_progress` / `done`) |
| `content/news/` | News: `YYYY-MM-DD-<slug>.de.md` + `.en.md` |
| `content/legal/` | Impressum (`TODO-IMPRESSUM` blockiert den Deploy) und Datenschutz |
| `static/img/screenshots/` | WebUI-Screenshots aus `dev/screenshots.py` |
| `static/img/client/` | Client-Screenshots aus der Test-VM (von Hand): `tray`, `warning`, `lock`, `request`, `windows` (`.webp`, optional `-de`/`-en`) |
