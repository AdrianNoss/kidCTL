#!/usr/bin/env python3
"""Banner für README.md/README.de.md im Design der Projektseite rendern.

Nutzt das CSS der Projektseite (website/themes/kidctl/assets/css/site.css), die Symbole aus dem Theme, die Texte
aus website/i18n/<lang>.yaml und die WebUI-Screenshots aus website/static/img/screenshots/. Ergebnis:
docs/img/<de|en>/banner-<light|dark>.webp (1280×400, doppelte Auflösung).

Aufruf (Dev-venv mit playwright + chromium, pyyaml, pillow):
    python dev/banner.py            # zeigt nur, was geschrieben würde
    python dev/banner.py --apply    # Bilder schreiben
"""
from __future__ import annotations

import argparse
import asyncio
import html
import io
import re
from pathlib import Path

import yaml
from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website"
THEME = SITE / "themes" / "kidctl"
OUT = ROOT / "docs" / "img"
W, H = 1280, 400


def icons() -> dict[str, str]:
    src = (THEME / "layouts" / "_partials" / "icon.html").read_text()
    return dict(re.findall(r'"([\w-]+)" `([^`]*)`', src))


def icon(paths: str) -> str:
    return ('<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>')


def page(lang: str, scheme: str) -> str:
    t = yaml.safe_load((SITE / "i18n" / f"{lang}.yaml").read_text())
    ic = icons()
    css = (THEME / "assets" / "css" / "site.css").read_text()
    shots = SITE / "static" / "img" / "screenshots" / lang / scheme
    badges = "".join(f'<li class="badge">{icon(ic[i])}{html.escape(t[k])}</li>'
                     for i, k in (("cloud-off", "badge_selfhosted"), ("eye-off", "badge_notelemetry"),
                                  ("code", "badge_opensource")))
    platforms = "".join(f'<li class="badge plat">{icon(ic[i])}{n}</li>'
                        for i, n in (("windows", "Windows 10/11"), ("linux", "Arch · Debian · Ubuntu")))
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{css}
html, body {{ width:{W}px; height:{H}px; overflow:hidden; }}
body {{ background:
  radial-gradient(60rem 30rem at 95% 110%, color-mix(in srgb, var(--primary-container) 85%, transparent), transparent 70%),
  radial-gradient(40rem 22rem at 0% 0%, color-mix(in srgb, var(--secondary-container) 55%, transparent), transparent 70%),
  var(--surface); }}
.banner {{ position:relative; height:100%; padding:52px 0 0 56px; }}
.text {{ position:relative; z-index:2; width:580px; }}
.brand {{ font-size:2.5rem; font-weight:600; gap:.875rem; margin-bottom:1.5rem; letter-spacing:-.01em; }}
.brand .mark {{ width:2.875rem; height:2.875rem; border-radius:var(--shape-md); }}
.brand .mark::after {{ width:4px; left:calc(50% - 2px); border-radius:2px; }}
.brand .mark::before {{ height:4px; top:calc(54% - 4px); left:calc(50% - 2px); border-radius:2px; }}
h1 {{ font-size:1.875rem; font-weight:400; margin:0 0 .375rem; line-height:1.2; white-space:nowrap; }}
.eyebrow {{ font-size:1rem; margin-bottom:1.75rem; }}
.badges {{ margin:0 0 .5rem; }}
.badges {{ max-width:540px; }}
.badge {{ font-size:.8125rem; padding:.25rem .7rem; }}
.badge.plat {{ background:var(--primary-container); color:var(--on-primary-container); }}
.shots {{ position:absolute; right:-110px; top:48px; width:600px; z-index:1; }}
.shots img {{ border-radius:var(--shape-lg); border:1px solid var(--outline-variant);
             box-shadow:0 12px 40px color-mix(in srgb, #000 22%, transparent); }}
.shots .desk {{ width:600px; }}
.shots .phone {{ position:absolute; left:-60px; top:64px; width:150px; border-radius:var(--shape-xl);
                border:6px solid var(--surface-container-highest); }}
</style></head><body><div class="banner">
<div class="text">
  <div class="brand"><span class="mark"></span>KidCTL</div>
  <h1>{html.escape(t["claim"])}</h1>
  <span class="eyebrow">{html.escape(t["hero_eyebrow"])}</span>
  <ul class="badges">{platforms}{badges}</ul>
</div>
<div class="shots">
  <img class="desk" src="{(shots / 'dashboard-desktop.webp').as_uri()}" alt="">
  <img class="phone" src="{(shots / 'child-today-mobile.webp').as_uri()}" alt="">
</div>
</div></body></html>"""


async def render(apply: bool) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for lang in ("de", "en"):
            for scheme in ("light", "dark"):
                target = OUT / lang / f"banner-{scheme}.webp"
                if not apply:
                    print(f"würde schreiben: {target.relative_to(ROOT)}")
                    continue
                ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2,
                                                color_scheme=scheme)
                pg = await ctx.new_page()
                tmp = OUT / lang / f".banner-{scheme}.html"
                tmp.write_text(page(lang, scheme))
                try:
                    await pg.goto(tmp.as_uri())
                    await pg.wait_for_load_state("networkidle")
                    Image.open(io.BytesIO(await pg.screenshot())).save(target, "WEBP", quality=88, method=6)
                finally:
                    tmp.unlink()
                    await ctx.close()
                print(f"geschrieben: {target.relative_to(ROOT)}")
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Bilder wirklich schreiben")
    asyncio.run(render(ap.parse_args().apply))
