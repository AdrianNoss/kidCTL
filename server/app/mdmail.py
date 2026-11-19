"""Markdown-Teilmenge für Newsletter: Überschriften (``##``/``###``), fett/kursiv, Listen, Links, Trennlinie.

``markdown-it-py`` ohne HTML (``html=False``), alles andere abgeschaltet (Bilder, Code, Zitate …); Links nur
``http``/``https``/``mailto``. Ausgabe mit Inline-Styles fürs Mail-Layout (``templates/mail/base.html``).
"""

from __future__ import annotations

from markdown_it import MarkdownIt

SANS = "Roboto,'Segoe UI','Helvetica Neue','Noto Sans',Arial,sans-serif"
INK, INK2, ACCENT, LINE = "#1a1b21", "#44464f", "#3b5ba9", "#e2e2e9"
ALLOWED_SCHEMES = ("http://", "https://", "mailto:")

_STYLE = {
    "p": f'class="k-ink2" style="margin:0 0 14px;font:15px/1.6 {SANS};color:{INK2};"',
    "h2": f'class="k-ink" style="margin:22px 0 10px;font:500 19px/1.35 {SANS};color:{INK};"',
    "h3": f'class="k-ink" style="margin:18px 0 8px;font:500 16px/1.4 {SANS};color:{INK};"',
    "ul": f'class="k-ink2" style="margin:0 0 14px;padding-left:22px;font:15px/1.6 {SANS};color:{INK2};"',
    "ol": f'class="k-ink2" style="margin:0 0 14px;padding-left:22px;font:15px/1.6 {SANS};color:{INK2};"',
    "li": 'style="margin:0 0 4px;"',
    "a": f'class="k-accent" style="color:{ACCENT};"',
    "hr": f'class="k-line" style="border:0;border-top:1px solid {LINE};margin:20px 0;"',
}


def _validate_link(url: str) -> bool:
    return url.strip().lower().startswith(ALLOWED_SCHEMES)


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.disable(["image", "code", "fence", "blockquote", "html_block", "html_inline", "backticks", "reference",
                "lheading", "autolink"])
    md.validateLink = _validate_link

    def open_tag(tag_of):
        def rule(self, tokens, idx, options, env):
            tag = tag_of(tokens[idx])
            return f"<{tag} {_STYLE.get(tag, '')}>"
        return rule

    def heading_tag(tok) -> str:
        return "h3" if tok.tag in ("h3", "h4", "h5", "h6") else "h2"    # # und ## -> h2, ### und tiefer -> h3

    md.add_render_rule("heading_open", open_tag(heading_tag))
    md.add_render_rule("heading_close", lambda self, tokens, idx, o, e: f"</{heading_tag(tokens[idx])}>\n")
    md.add_render_rule("paragraph_open", lambda self, tokens, idx, o, e: "" if tokens[idx].hidden else f"<p {_STYLE['p']}>")
    for name, tag in (("bullet_list_open", "ul"), ("ordered_list_open", "ol"), ("list_item_open", "li")):
        md.add_render_rule(name, open_tag(lambda tok, tag=tag: tag))

    def link_open(self, tokens, idx, options, env):
        href = tokens[idx].attrGet("href") or ""
        from html import escape
        return f'<a href="{escape(href, quote=True)}" {_STYLE["a"]}>'
    md.add_render_rule("link_open", link_open)
    md.add_render_rule("hr", lambda self, tokens, idx, o, e: f"<hr {_STYLE['hr']}>\n")
    return md


_MD = _parser()


def to_html(source: str) -> str:
    """Markdown-Quelle -> sicheres HTML mit Inline-Styles."""
    return _MD.render(source or "")


def preheader(source: str, limit: int = 140) -> str:
    """Erste Textzeile ohne Markdown-Zeichen (Vorschauzeile im Postfach)."""
    import re
    for line in (source or "").splitlines():
        text = re.sub(r"[#*_`>\[\]]|\(https?://[^)]*\)|^\s*[-+]\s+|^\s*\d+\.\s+", "", line).strip()
        if text and not set(text) <= set("-*_ "):
            return text[:limit]
    return ""
