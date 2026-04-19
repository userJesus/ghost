"""Rasterize the `@keyframes pixel-ghost` animation (from web/pixel-ghost.css)
into an animated GIF for use in the GitHub README.

The CSS uses the classic pixel-art-via-box-shadow trick: a single 1×1 element
with many `box-shadow: Xpx Ypx #color` entries — each one is one pixel of the
ghost. There are 7 keyframe snapshots (0%, 14.28%, 28.57%, …) that together
form the looping float animation; duration is 1.4 s with `steps(1)`.

Output: .github/assets/pixel-ghost.gif
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "web" / "pixel-ghost.css"
OUT = ROOT / ".github" / "assets" / "pixel-ghost.gif"

# Visual size of each pixel-art pixel in the final PNG/GIF.
PIXEL_SIZE = 10
# Padding around the ghost, in CSS pixels.
PAD = 2
# Frame duration in ms (1.4 s / 7 frames).
FRAME_MS = 200
# Transparent background to blend with any README theme.
BG = (19, 19, 19, 255)  # Ghost's mica-base #131313


def extract_frames(css: str) -> list[list[tuple[int, int, str]]]:
    """Return list of frames, each a list of (x, y, hex_color) tuples."""
    m = re.search(r"@keyframes\s+pixel-ghost\s*\{(.+?)\}\s*(?=\.pixel-ghost)", css, flags=re.DOTALL)
    if not m:
        raise RuntimeError("Could not locate @keyframes pixel-ghost in CSS.")
    body = m.group(1)
    # Each frame: "0.0% { box-shadow: Xpx Ypx #color, ...; }"
    frame_re = re.compile(r"([\d.]+)%\s*\{\s*box-shadow:\s*(.+?);\s*\}", flags=re.DOTALL)
    shadow_re = re.compile(r"(-?\d+)px\s+(-?\d+)px\s+(#[0-9a-fA-F]{3,8})")
    frames: list[tuple[float, list[tuple[int, int, str]]]] = []
    for frame_m in frame_re.finditer(body):
        pct = float(frame_m.group(1))
        shadows = [(int(x), int(y), c) for x, y, c in shadow_re.findall(frame_m.group(2))]
        frames.append((pct, shadows))
    frames.sort(key=lambda t: t[0])
    return [shadows for _, shadows in frames]


def normalize(frames: list[list[tuple[int, int, str]]]) -> tuple[list[list[tuple[int, int, str]]], int, int]:
    """Shift all coordinates so the smallest is (0,0), return (frames, width, height)."""
    all_x = [x for f in frames for x, _, _ in f]
    all_y = [y for f in frames for _, y, _ in f]
    min_x, min_y = min(all_x), min(all_y)
    max_x, max_y = max(all_x), max(all_y)
    w = max_x - min_x + 1 + 2 * PAD
    h = max_y - min_y + 1 + 2 * PAD
    shifted = [
        [(x - min_x + PAD, y - min_y + PAD, c) for x, y, c in f]
        for f in frames
    ]
    return shifted, w, h


def hex_to_rgba(h: str) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return (r, g, b, a)


def render_frame(shadows: list[tuple[int, int, str]], w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w * PIXEL_SIZE, h * PIXEL_SIZE), BG)
    px = img.load()
    for x, y, color in shadows:
        rgba = hex_to_rgba(color)
        for dy in range(PIXEL_SIZE):
            for dx in range(PIXEL_SIZE):
                px[x * PIXEL_SIZE + dx, y * PIXEL_SIZE + dy] = rgba
    return img


def main() -> None:
    css = CSS.read_text(encoding="utf-8")
    frames_raw = extract_frames(css)
    frames_norm, w, h = normalize(frames_raw)
    print(f"[pixel-ghost] {len(frames_norm)} frames, grid {w}×{h} → {w*PIXEL_SIZE}×{h*PIXEL_SIZE}px")

    pil_frames = [render_frame(f, w, h) for f in frames_norm]

    # GIF needs paletted ("P") frames; quantize uniformly so colors match across frames.
    # Use the first frame's palette for consistency.
    quantized = [im.convert("RGB").quantize(colors=64, method=Image.Quantize.MEDIANCUT) for im in pil_frames]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        OUT,
        save_all=True,
        append_images=quantized[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"[pixel-ghost] wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
