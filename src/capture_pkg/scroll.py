import time

import mss
import pyautogui
from PIL import Image

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


def list_monitors() -> list[dict]:
    """Return list of individual monitors (excluding the 'all monitors' entry)."""
    with mss.mss() as sct:
        return [
            {
                "index": i,
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
                "label": f"Monitor {i} — {m['width']}x{m['height']} @ ({m['left']},{m['top']})",
            }
            for i, m in enumerate(sct.monitors[1:], start=1)
        ]


def capture_monitor(monitor: dict) -> Image.Image:
    region = {
        "left": monitor["left"],
        "top": monitor["top"],
        "width": monitor["width"],
        "height": monitor["height"],
    }
    with mss.mss() as sct:
        shot = sct.grab(region)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _focus_monitor_center(monitor: dict) -> tuple[int, int]:
    x = monitor["left"] + monitor["width"] // 2
    y = monitor["top"] + monitor["height"] // 2
    pyautogui.moveTo(x, y, duration=0.2)
    pyautogui.click()
    time.sleep(0.15)
    return x, y


def _bottom_half_bytes(img: Image.Image) -> bytes:
    """Return bytes from only the bottom 60% of the image.

    Avoids false 'page ended' detection caused by fixed headers/navbars
    that don't change between scrolls.
    """
    w, h = img.size
    cropped = img.crop((0, int(h * 0.4), w, h))
    return cropped.tobytes()


def _images_equal(a: Image.Image, b: Image.Image, threshold: int = 200) -> bool:
    """Similarity check comparing bottom portions of images."""
    if a.size != b.size:
        return False
    ba = _bottom_half_bytes(a)
    bb = _bottom_half_bytes(b)
    if ba == bb:
        return True
    step = 50
    diff = sum(1 for x, y in zip(ba[::step], bb[::step]) if x != y)
    return diff < threshold


def scroll_and_capture(
    monitor: dict,
    max_scrolls: int = 40,
    delay: float = 0.5,
    status_callback=None,
) -> list[Image.Image]:
    """
    Focus monitor, scroll to top, then capture + scroll down until page stops changing.
    Uses PageDown for consistent full-page-height scrolls.
    Returns list of screenshots in order.
    """
    if status_callback:
        status_callback("Focando monitor...")
    _focus_monitor_center(monitor)

    if status_callback:
        status_callback("Subindo ao topo da página...")
    pyautogui.hotkey("ctrl", "Home")
    time.sleep(0.6)

    screenshots: list[Image.Image] = []
    previous: Image.Image | None = None
    stable_count = 0

    for i in range(max_scrolls):
        if status_callback:
            status_callback(f"Capturando {i + 1}/{max_scrolls}...")
        img = capture_monitor(monitor)

        if previous is not None and _images_equal(img, previous):
            stable_count += 1
            if stable_count >= 2:
                if status_callback:
                    status_callback(f"Fim da página detectado (captura {i + 1}).")
                break
        else:
            stable_count = 0

        screenshots.append(img)
        previous = img

        pyautogui.press("pagedown")
        time.sleep(delay)

    return screenshots


def stitch_vertical(images: list[Image.Image], max_height: int = 6000) -> Image.Image | None:
    """Concatenate images vertically; cap final height to keep GPT payload reasonable."""
    if not images:
        return None
    width = images[0].width
    total_height = sum(img.height for img in images)

    stitched = Image.new("RGB", (width, total_height), "white")
    y = 0
    for img in images:
        stitched.paste(img, (0, y))
        y += img.height

    if stitched.height > max_height:
        ratio = max_height / stitched.height
        new_size = (int(stitched.width * ratio), max_height)
        stitched = stitched.resize(new_size, Image.LANCZOS)

    return stitched
