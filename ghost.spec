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
        # Ghost internal modules — pre-refactor flat layout (still valid as shims).
        "src.api",
        "src.bootstrap",
        "src.gpt_client",
        "src.history",
        "src.win_focus",
        "src.sensitive",
        "src.logging_config",
        "src.platform_adapter",
        "src.capture",
        "src.scroll_capture",
        "src.meeting",
        "src.meeting_processor",
        "src.voice",
        "src.clone",
        "src.updater",
        "src.parser",
        # Post-refactor architecture packages.
        "src.infra",
        "src.infra.paths",
        "src.infra.logging_setup",
        "src.services",
        "src.services.update_service",
        "src.services.settings_service",
        "src.services.history_service",
        "src.platform",
        "src.platform.adapter",
        "src.platform.windows",
        "src.platform.windows.focus",
        "src.platform.windows.preflight",
        "src.integrations",
        "src.integrations.openai_client",
        "src.integrations.github_releases",
        "src.domain",
        "src.domain.sensitive_scan",
        "src.domain.markdown_parser",
        "src.domain.version_compare",
        "src.capture_pkg",
        "src.capture_pkg.screenshot",
        "src.capture_pkg.scroll",
        "src.capture_pkg.region_picker",
        "src.recording",
        "src.recording.meeting_recorder",
        "src.recording.meeting_processor",
        "src.recording.voice_recorder",
        "src.cloner",
        "src.cloner.web_cloner",
        # Region selector runs as a subprocess (`Ghost.exe --region-selector`)
        # so tkinter gets its own process / main thread. PyInstaller doesn't
        # follow the Popen call chain into main.py's sub-mode dispatch, so
        # pin both modules and tkinter here. Keep tkinter OUT of `excludes`.
        "src.region_selector_cli",
        "src.region_selector",
        "tkinter",
        "tkinter.ttk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim things not used at runtime to keep size reasonable.
        # IMPORTANT: do NOT add tkinter — capture_area's subprocess needs it.
        # Excluding it shipped as a crash in 1.0.28/1.0.29 where clicking
        # "Área" showed a traceback dialog and locked the chat input on
        # busy=true until the dialog was dismissed.
        "matplotlib",
        "scipy",
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
