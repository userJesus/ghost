# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Ghost (macOS).
# Build: pyinstaller --noconfirm ghost_mac.spec
#
# Produces: dist/Ghost.app (bundled .app) and dist/Ghost/ (onedir internals)
#
# Why PyInstaller instead of py2app?
#   py2app rejects install_requires coming from pyproject.toml [project].dependencies
#   with "install_requires is no longer supported", which is awkward to work around.
#   PyInstaller is the same tool we use for Windows, supports macOS .app bundles via
#   BUNDLE(...), and handles the cross-platform build uniformly.
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).resolve()

block_cipher = None

# Packages that call importlib.metadata.version() at import time.
_metadata_pkgs = ["imageio", "openai", "pywebview", "mss", "soundfile", "soundcard"]
_metadata = []
for pkg in _metadata_pkgs:
    try:
        _metadata += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "web"), "web"),
        (str(ROOT / "assets" / "icon_ghost.svg"), "assets"),
        (str(ROOT / "assets" / "icon.icns"), "assets"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "README.md"), "."),
    ] + _metadata,
    hiddenimports=[
        # pywebview cocoa backend + PyObjC helpers
        "webview.platforms.cocoa",
        "AppKit",
        "Foundation",
        "WebKit",
        "Quartz",
        "objc",
        # Ghost internal modules
        "src.api",
        "src.gpt_client",
        "src.history",
        "src.mac_focus",
        "src.sensitive",
        "src.logging_config",
        "src.platform_adapter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only bits we explicitly don't ship on Mac.
        "win32api",
        "win32con",
        "win32gui",
        "win32process",
        "win32timezone",
        "win32com",
        "pywin32",
        "pywintypes",
        "pythoncom",
        "src.win_focus",
        # Trim to keep bundle size reasonable.
        "matplotlib",
        "scipy",
        "tkinter",
        "test",
        "tests",
        "pytest",
        "reportlab",
        "svglib",
        "PyInstaller",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ghost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed (no terminal)
    disable_windowed_traceback=False,
    target_arch=None,        # native (arm64 on Apple Silicon runner)
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Ghost",
)

# Wrap the onedir output into a proper .app bundle.
app = BUNDLE(
    coll,
    name="Ghost.app",
    icon=str(ROOT / "assets" / "icon.icns"),
    bundle_identifier="io.github.userjesus.ghost",
    version="1.0.0",
    info_plist={
        "CFBundleName": "Ghost",
        "CFBundleDisplayName": "Ghost",
        "CFBundleExecutable": "Ghost",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHumanReadableCopyright": "Copyright © 2026 Jesus Oliveira. NCSAL v1.0.",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "LSUIElement": False,
        "NSCameraUsageDescription":
            "Ghost may access camera-like APIs for screen capture.",
        "NSMicrophoneUsageDescription":
            "Ghost records audio for meeting transcription and voice input.",
        "NSScreenCaptureUsageDescription":
            "Ghost captures the screen to help answer your questions.",
    },
)
