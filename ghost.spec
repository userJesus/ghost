# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Ghost (Windows).
# Build: pyinstaller --noconfirm ghost.spec
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata, collect_submodules

ROOT = Path(SPECPATH).resolve()

block_cipher = None

# Packages that call importlib.metadata.version() at import time and need their
# dist-info preserved in the bundle.
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
        (str(ROOT / "assets" / "icon.ico"), "assets"),
        (str(ROOT / "assets" / "icon_ghost.svg"), "assets"),
    ] + _metadata,
    hiddenimports=[
        # pywebview backends and helpers (dynamic loading).
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "proxy_tools",
        # win32 pieces pywin32 uses.
        "win32timezone",
        # Ghost internal modules (captured defensively).
        "src.api",
        "src.gpt_client",
        "src.history",
        "src.win_focus",
        "src.sensitive",
        "src.logging_config",
        "src.platform_adapter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim things not used at runtime to keep size reasonable.
        "matplotlib",
        "scipy",
        "tkinter",
        "test",
        "tests",
        "pytest",
        "reportlab",  # only used by dev scripts (docs, icons)
        "svglib",
        "PyInstaller",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),
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
