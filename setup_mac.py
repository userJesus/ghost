"""py2app bundler for the Ghost macOS build.

Build:
    python setup_mac.py py2app            # full .app bundle in dist/
    python setup_mac.py py2app -A         # alias build (fast, dev-loop)

Prereqs (macOS host):
    pip install -r dev-requirements.txt
    pip install py2app
    iconutil -c icns assets/icon.iconset -o assets/icon.icns

Packaging .pkg (with BlackHole driver + app):
    bash scripts/build_mac.sh

Author: Jesus Oliveira <contato.jesusoliveira@gmail.com>
LinkedIn: https://www.linkedin.com/in/ojesus
GitHub:   https://github.com/userJesus
License: NCSAL v1.0 (non-commercial source-available) — see LICENSE
"""
from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "icon.icns"

# Pull version from the single source of truth.
sys.path.insert(0, str(ROOT / "src"))
from version import __version__ as APP_VERSION  # noqa: E402
sys.path.pop(0)

APP_NAME = "Ghost"
APP = ["main.py"]

def _collect_web_files() -> list[tuple[str, list[str]]]:
    """Walk web/ and emit py2app-compatible (dest_dir, [files]) pairs preserving structure."""
    out: dict[str, list[str]] = {}
    web = ROOT / "web"
    for f in web.rglob("*"):
        if not f.is_file() or "__pycache__" in str(f):
            continue
        rel_parent = f.parent.relative_to(ROOT).as_posix()
        out.setdefault(rel_parent, []).append(str(f))
    return [(d, files) for d, files in out.items()]


DATA_FILES = _collect_web_files() + [
    ("assets", [str(ROOT / "assets" / "icon_ghost.svg")]),
    (".", [str(ROOT / "LICENSE"), str(ROOT / "README.md")]),
]

# macOS-specific: pywebview uses the cocoa backend via PyObjC.
PY2APP_OPTIONS = {
    "argv_emulation": False,
    "iconfile": str(ICON) if ICON.exists() else None,
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "io.github.userjesus.ghost",
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleExecutable": APP_NAME,
        "NSHumanReadableCopyright": "Copyright © 2026 Jesus Oliveira. NCSAL v1.0.",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSCameraUsageDescription": "Ghost may access camera-like APIs for screen capture.",
        "NSMicrophoneUsageDescription": "Ghost records audio for meeting transcription and voice input.",
        "NSScreenCaptureUsageDescription": "Ghost captures the screen to help answer your questions.",
        "LSUIElement": False,
    },
    "packages": [
        "webview",
        "openai",
        "PIL",
        "mss",
        "soundcard",
        "soundfile",
        "numpy",
        "pynput",
        "imageio",
        "src",
    ],
    "includes": [
        "webview.platforms.cocoa",
    ],
    "excludes": [
        "win32gui",
        "win32process",
        "win32api",
        "win32con",
        "pywin32",
        "pythoncom",
        "pywintypes",
        "PyInstaller",
        "svglib",
        "reportlab",
        "matplotlib",
        "scipy",
        "tkinter",
    ],
    "optimize": 1,
}

if sys.platform != "darwin":
    print("[setup_mac] WARNING: py2app only builds on macOS. Run this file from a Mac.",
          file=sys.stderr)

setup(
    name=APP_NAME,
    version=APP_VERSION,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": PY2APP_OPTIONS},
    setup_requires=["py2app"],
    author="Jesus Oliveira",
    author_email="contato.jesusoliveira@gmail.com",
    url="https://github.com/userJesus/ghost",
    license="NCSAL v1.0 (non-commercial source-available)",
    description="Ghost — desktop AI assistant. Non-commercial use only.",
)
