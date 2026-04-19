"""Generate platform icons from assets/icon_ghost.svg.

Outputs:
  - assets/icon.ico   (Windows, multi-res)
  - assets/icon_1024.png, icon_512.png, ... icon_16.png
  - assets/icon.iconset/   (Mac — folder for `iconutil -c icns`)

Run: python scripts/make_icons.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFilter
from svg.path import CubicBezier, Line, Move, QuadraticBezier, parse_path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SRC_SVG = ASSETS / "icon_ghost.svg"

# Visual identity — same gradient as the docked icon in the app.
ACCENT_1 = (0x61, 0xDB, 0xB4)   # --accent
ACCENT_2 = (0x3C, 0xB8, 0x95)   # gradient tail
ICON_FG = (0xFD, 0xFD, 0xFD)    # white ghost fill

ICO_SIZES = [16, 32, 48, 64, 128, 256]
MAC_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def _parse_svg() -> tuple[list[list[tuple[float, float]]], float, float]:
    """Parse the ghost SVG into closed subpath polygons + viewBox dims.
    Each subpath is a flat list of (x, y) points sampled along the curves.
    svglib/cairosvg both fail (cairo DLL on Windows, svglib bbox bug on curves),
    so we rasterize manually: parse-path → subdivide bezier → Pillow polygon.
    """
    text = SRC_SVG.read_text(encoding="utf-8")
    vb_m = re.search(r'viewBox="([^"]+)"', text)
    if vb_m:
        vb = vb_m.group(1).split()
        vb_w, vb_h = float(vb[2]), float(vb[3])
    else:
        vb_w, vb_h = 561.0, 695.0
    d_m = re.search(r'<path[^>]*\bd="([^"]+)"', text)
    if not d_m:
        raise RuntimeError("icon_ghost.svg has no <path d=...>")
    d = d_m.group(1)

    # Split into subpaths at each M/Z boundary so we can fill-rule them.
    subpaths_src = [s for s in re.split(r"(?=M|m)", d) if s.strip()]
    subpaths: list[list[tuple[float, float]]] = []
    for src in subpaths_src:
        pts: list[tuple[float, float]] = []
        try:
            segments = parse_path(src)
        except Exception:
            continue
        for seg in segments:
            if isinstance(seg, Move):
                pts.append((seg.end.real, seg.end.imag))
            elif isinstance(seg, Line):
                pts.append((seg.end.real, seg.end.imag))
            elif isinstance(seg, (CubicBezier, QuadraticBezier)):
                # Sample ~30 points per curve — smooth enough at icon resolutions.
                for i in range(1, 31):
                    p = seg.point(i / 30.0)
                    pts.append((p.real, p.imag))
            else:  # Arc, Close — just take endpoint.
                try:
                    p = seg.point(1.0)
                    pts.append((p.real, p.imag))
                except Exception:
                    pass
        if len(pts) >= 3:
            subpaths.append(pts)
    return subpaths, vb_w, vb_h


_GHOST_CACHE: tuple[list[list[tuple[float, float]]], float, float] | None = None


def render_svg_to_png(size: int) -> Image.Image:
    """Rasterize the ghost (white body, cut-out eyes) at `size` on longest side."""
    global _GHOST_CACHE
    if _GHOST_CACHE is None:
        _GHOST_CACHE = _parse_svg()
    subpaths, vb_w, vb_h = _GHOST_CACHE

    scale = size / max(vb_w, vb_h)
    w, h = int(round(vb_w * scale)), int(round(vb_h * scale))
    # Render at 2x then downscale for smoother edges (super-sampling).
    SS = 2
    w2, h2 = w * SS, h * SS

    # Body = first subpath; eyes = remaining subpaths.
    body_pts = [(x * scale * SS, y * scale * SS) for x, y in subpaths[0]]
    mask = Image.new("L", (w2, h2), 0)
    ImageDraw.Draw(mask).polygon(body_pts, fill=255)
    for sub in subpaths[1:]:
        hole = [(x * scale * SS, y * scale * SS) for x, y in sub]
        ImageDraw.Draw(mask).polygon(hole, fill=0)

    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (w2, h2), (*ICON_FG, 255)), (0, 0), mask)
    img = img.resize((w, h), Image.LANCZOS)
    return img


def make_gradient_square(size: int, radius_ratio: float = 0.14) -> Image.Image:
    """Rounded square with the brand gradient + inset top highlight (matches docked)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Diagonal gradient from ACCENT_1 (top-left) to ACCENT_2 (bottom-right) — 135deg.
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1)) if size > 1 else 0
            r = round(ACCENT_1[0] * (1 - t) + ACCENT_2[0] * t)
            g = round(ACCENT_1[1] * (1 - t) + ACCENT_2[1] * t)
            b = round(ACCENT_1[2] * (1 - t) + ACCENT_2[2] * t)
            px[x, y] = (r, g, b)
    # Rounded-square alpha mask (same radius ratio as CSS: 8px / 56px ≈ 0.14).
    radius = max(2, int(size * radius_ratio))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    img.paste(grad, (0, 0), mask)
    # Inset top highlight: `box-shadow: inset 0 1px 0 rgba(255,255,255,0.15)`.
    hl_thickness = max(1, size // 64)
    hl = Image.new("RGBA", (size, hl_thickness), (255, 255, 255, 38))
    hl_mask = mask.crop((0, 0, size, hl_thickness))
    img.paste(hl, (0, 0), hl_mask)
    return img


def compose_icon(size: int) -> Image.Image:
    """Brand rounded-square + white ghost (55% canvas) with soft white glow — matches docked."""
    base = make_gradient_square(size)
    glyph_size = max(8, int(size * 0.55))  # same proportion as CSS `.docked-glyph { width: 55%; }`
    glyph = render_svg_to_png(glyph_size)
    # Force pure white fill.
    pixels = glyph.load()
    for y in range(glyph.height):
        for x in range(glyph.width):
            r, g, b, a = pixels[x, y]
            if a > 0:
                pixels[x, y] = (*ICON_FG, a)
    offset_x = (size - glyph.width) // 2
    offset_y = (size - glyph.height) // 2
    # Soft white glow matching CSS `filter: drop-shadow(0 0 6px rgba(255,255,255,0.35))`.
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    glow_layer = Image.new("RGBA", (glyph.width, glyph.height), (0, 0, 0, 0))
    gl_pixels = glow_layer.load()
    for y in range(glyph.height):
        for x in range(glyph.width):
            _, _, _, a = pixels[x, y]
            if a > 0:
                gl_pixels[x, y] = (255, 255, 255, int(a * 0.35))
    glow.paste(glow_layer, (offset_x, offset_y), glow_layer)
    glow = glow.filter(ImageFilter.GaussianBlur(max(1.5, size / 42)))
    out = Image.alpha_composite(base, glow)
    out.paste(glyph, (offset_x, offset_y), glyph)
    return out


def main() -> int:
    if not SRC_SVG.exists():
        print(f"ERROR: missing {SRC_SVG}", file=sys.stderr)
        return 1
    ASSETS.mkdir(parents=True, exist_ok=True)

    # Per-size PNGs (used for ICO and for Mac iconset).
    pngs: dict[int, Image.Image] = {}
    for s in sorted(set(ICO_SIZES + MAC_SIZES)):
        print(f"[icons] rendering {s}x{s}...", flush=True)
        pngs[s] = compose_icon(s)
        pngs[s].save(ASSETS / f"icon_{s}.png", format="PNG")

    # Windows ICO (multi-res, single file).
    ico_path = ASSETS / "icon.ico"
    base = pngs[256]
    base.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[pngs[s] for s in ICO_SIZES if s != 256],
    )
    print(f"[icons] wrote {ico_path}")

    # Mac iconset folder (convert with: iconutil -c icns assets/icon.iconset)
    iconset = ASSETS / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    pairs = [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for size, name in pairs:
        pngs[size].save(iconset / name, format="PNG")
    print(f"[icons] wrote {iconset} (convert to .icns on macOS with iconutil)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
