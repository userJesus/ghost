import io
import base64
import mss
from PIL import Image


def capture_fullscreen() -> Image.Image:
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def capture_region(x: int, y: int, w: int, h: int) -> Image.Image:
    with mss.mss() as sct:
        region = {"left": x, "top": y, "width": w, "height": h}
        shot = sct.grab(region)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def image_to_base64(img: Image.Image, max_dim: int = 1600) -> str:
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def image_to_data_url(img: Image.Image, max_dim: int = 1600) -> str:
    return f"data:image/png;base64,{image_to_base64(img, max_dim)}"
