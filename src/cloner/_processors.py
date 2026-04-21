"""HTML + CSS + srcset rewriters, `_process_asset`, `TAG_URL_ATTRS`."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ._config import CSS_IMPORT_RE, CSS_URL_RE
from ._context import _Ctx, _fetch
from ._paths import _asset_rel_path, _relative_between


def _rewrite_css_text(css_text: str, css_absolute_url: str, css_local_rel: str,
                       ctx: _Ctx) -> str:
    """Rewrite url(...) and @import in a CSS document and recursively fetch referenced assets."""

    def _replace_url(match: re.Match) -> str:
        quote = match.group(1)
        raw = (match.group(2) or "").strip()
        if not raw or raw.startswith("data:") or raw.startswith("#"):
            return match.group(0)
        absolute = urljoin(css_absolute_url, raw)
        local = ctx.register(absolute)
        if not local:
            return match.group(0)
        rel = _relative_between(css_local_rel, local)
        # Queue for download (will be processed by main worker)
        with ctx.lock:
            if absolute not in ctx.seen_css_urls:
                ctx.seen_css_urls.add(absolute)
        return f"url({quote}{rel}{quote})"

    def _replace_import(match: re.Match) -> str:
        quote = match.group(1)
        raw = (match.group(2) or "").strip()
        if not raw or raw.startswith("data:"):
            return match.group(0)
        absolute = urljoin(css_absolute_url, raw)
        local = ctx.register(absolute)
        if not local:
            return match.group(0)
        rel = _relative_between(css_local_rel, local)
        with ctx.lock:
            if absolute not in ctx.seen_css_urls:
                ctx.seen_css_urls.add(absolute)
        return f"@import {quote}{rel}{quote}"

    out = CSS_IMPORT_RE.sub(_replace_import, css_text)
    out = CSS_URL_RE.sub(_replace_url, out)
    return out


def _process_asset(ctx: _Ctx, absolute_url: str, local_rel: str) -> bool:
    """Download an asset. If it's CSS, recursively collect its referenced assets."""
    if ctx.cancel:
        return False
    with ctx.lock:
        ctx.attempted.add(absolute_url)
    result = _fetch(ctx, absolute_url)
    if result is None:
        return False
    data, ctype = result

    is_css = (
        "css" in ctype
        or local_rel.endswith(".css")
        or absolute_url.split("?", 1)[0].lower().endswith(".css")
    )

    if is_css:
        try:
            text = data.decode("utf-8", errors="replace")
            rewritten = _rewrite_css_text(text, absolute_url, local_rel, ctx)
            data = rewritten.encode("utf-8", errors="replace")
        except Exception as e:
            ctx.errors.append(f"css-rewrite {type(e).__name__} {absolute_url[:100]}")

    with ctx.lock:
        ctx.to_write[local_rel] = data
    return True


# ---- HTML asset extraction / rewriting -------------------------------------

# Tag/attr pairs that contain a single URL
TAG_URL_ATTRS: list[tuple[str, str]] = [
    ("link", "href"),
    ("script", "src"),
    ("img", "src"),
    ("img", "data-src"),
    ("source", "src"),
    ("video", "src"),
    ("video", "poster"),
    ("audio", "src"),
    ("iframe", "src"),
    ("embed", "src"),
    ("object", "data"),
    ("use", "href"),
    ("use", "xlink:href"),
    ("image", "href"),
    ("image", "xlink:href"),
]


def _process_srcset(value: str, base_url: str, ctx: _Ctx, html_local: str = "index.html") -> str:
    """Rewrite an HTML `srcset` attribute."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    out_parts = []
    for part in parts:
        bits = part.split(None, 1)
        url_part = bits[0]
        descriptor = bits[1] if len(bits) > 1 else ""
        if url_part.startswith("data:"):
            out_parts.append(part)
            continue
        absolute = urljoin(base_url, url_part)
        local = ctx.register(absolute)
        if local is None:
            out_parts.append(part)
            continue
        rel = _relative_between(html_local, local)
        out_parts.append(f"{rel} {descriptor}".strip())
    return ", ".join(out_parts)


def _process_html(html: str, base_url: str, ctx: _Ctx) -> str:
    """Parse HTML, collect + register all asset URLs, return rewritten HTML."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    html_local = "index.html"

    # Strip <base> (we're resolving URLs ourselves); save original href for base_url fallback
    for base_tag in soup.find_all("base"):
        href = base_tag.get("href")
        if href:
            base_url = urljoin(base_url, href)
        base_tag.decompose()

    def rewrite_single(absolute: str) -> str | None:
        local = ctx.register(absolute)
        if not local:
            return None
        return _relative_between(html_local, local)

    # Standard single-URL attributes
    for tag_name, attr in TAG_URL_ATTRS:
        for tag in soup.find_all(tag_name):
            val = tag.get(attr)
            if not val:
                continue
            val = val.strip()
            if val.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
                continue
            absolute = urljoin(base_url, val)
            if not urlparse(absolute).scheme.startswith("http"):
                continue
            rel = rewrite_single(absolute)
            if rel:
                tag[attr] = rel
                # Drop SRI hashes (the file contents may differ post-rewrite)
                if tag.has_attr("integrity"):
                    del tag["integrity"]
                # Crossorigin is irrelevant for local files
                if tag.has_attr("crossorigin"):
                    del tag["crossorigin"]

    # srcset on <img> and <source>
    for tag in soup.find_all(["img", "source"]):
        for attr in ("srcset", "data-srcset"):
            val = tag.get(attr)
            if val:
                tag[attr] = _process_srcset(val, base_url, ctx, html_local)

    # Inline style attributes
    for tag in soup.find_all(style=True):
        style_val = tag["style"]
        new_val = _rewrite_css_text(style_val, base_url, html_local, ctx)
        tag["style"] = new_val

    # Inline <style> blocks
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string = _rewrite_css_text(style_tag.string, base_url, html_local, ctx)

    # Meta refresh redirects → neutralize (we want to stay on the cloned page)
    for meta in soup.find_all("meta", attrs={"http-equiv": True}):
        if (meta.get("http-equiv") or "").lower() == "refresh":
            meta.decompose()

    # Strip CSP meta (local file:// contexts handle it differently)
    for meta in soup.find_all("meta"):
        he = (meta.get("http-equiv") or "").lower()
        if he in ("content-security-policy", "content-security-policy-report-only"):
            meta.decompose()

    # Add a banner comment + UTF-8 charset meta (ensures offline rendering is predictable)
    if soup.head is not None:
        charset = soup.head.find("meta", charset=True)
        if not charset:
            meta = soup.new_tag("meta", charset="utf-8")
            soup.head.insert(0, meta)

    return str(soup)


# ---- Playwright (optional, on-demand) --------------------------------------
