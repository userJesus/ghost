"""Path + slug helpers for building local asset filenames."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


def clones_dir() -> Path:
    p = Path.home() / "Desktop" / "Ghost-Clones"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(text: str, maxlen: int = 40) -> str:
    text = re.sub(r"[^A-Za-z0-9\-_.]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-.")
    return text[:maxlen] or "page"


def _folder_name(url: str) -> str:
    u = urlparse(url)
    host = _slug(u.netloc or "site", 60)
    path_part = _slug((u.path or "").strip("/").replace("/", "-"), 30)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    if path_part and path_part != "index":
        return f"{host}-{path_part}-{ts}"
    return f"{host}-{ts}"


def _asset_rel_path(absolute_url: str) -> str | None:
    """Map an absolute URL to a relative filesystem path under _assets/."""
    try:
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            return None
        host = _slug(parsed.netloc.replace(":", "_"), 80)
        path = parsed.path or "/"
        if path.endswith("/") or path == "":
            path = path + "index.html"
        # Query strings differentiate cached variants
        if parsed.query:
            qhash = hashlib.md5(parsed.query.encode("utf-8", "ignore")).hexdigest()[:8]
            p = PurePosixPath(path)
            stem = p.stem or "file"
            ext = p.suffix
            parent = str(p.parent).lstrip("/").strip(".")
            new_name = f"{stem}.{qhash}{ext}" if ext else f"{stem}.{qhash}"
            path = f"/{parent}/{new_name}" if parent else f"/{new_name}"
        # Sanitize each path segment independently (preserve folder structure)
        segments = [_slug(seg, 80) for seg in path.lstrip("/").split("/") if seg]
        if not segments:
            segments = ["index.html"]
        return "_assets/" + host + "/" + "/".join(segments)
    except Exception:
        return None


def _relative_between(from_rel: str, to_rel: str) -> str:
    """Compute a relative URL path from from_rel (a file) to to_rel (a file)."""
    try:
        from_parts = PurePosixPath(from_rel).parent.parts
        to_parts = PurePosixPath(to_rel).parts
        i = 0
        while i < len(from_parts) and i < len(to_parts) - 1 and from_parts[i] == to_parts[i]:
            i += 1
        ups = [".."] * (len(from_parts) - i)
        downs = list(to_parts[i:])
        return "/".join(ups + downs) if (ups + downs) else to_parts[-1]
    except Exception:
        return to_rel

