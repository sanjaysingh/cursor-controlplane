# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: from repo root run:
#   pip install -r requirements-build.txt
#   pyinstaller cursor-controlplane.spec

import os
from pathlib import Path

spec_root = Path(os.path.dirname(os.path.abspath(SPEC)))
static_dir = spec_root / "control_plane" / "static"

block_cipher = None

a = Analysis(
    [str(spec_root / "run.py")],
    pathex=[str(spec_root)],
    binaries=[],
    datas=[(str(static_dir), "control_plane/static")] if static_dir.is_dir() else [],
    hiddenimports=["uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan", "uvicorn.lifespan.on"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="cursor-controlplane",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
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
