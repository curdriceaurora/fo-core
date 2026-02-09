# -*- mode: python ; coding: utf-8 -*-
# Auto-generated baseline spec file for PyInstaller.
# Edit scripts/build_config.py to adjust build settings.

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_config import (  # noqa: E402
    APP_NAME,
    APP_VERSION,
    DATA_FILES,
    EXCLUDES,
    HIDDEN_IMPORTS,
    current_arch,
    current_platform,
)

platform = current_platform()
arch = current_arch()
suffix = ".exe" if platform == "windows" else ""
output_name = f"{APP_NAME}-{APP_VERSION}-{platform}-{arch}{suffix}"
strip_symbols = platform != "windows"

block_cipher = None

a = Analysis(
    ["src/file_organizer/cli/main.py"],
    pathex=["src"],
    binaries=[],
    datas=list(DATA_FILES),
    hiddenimports=list(HIDDEN_IMPORTS),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=list(EXCLUDES),
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=output_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=strip_symbols,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
