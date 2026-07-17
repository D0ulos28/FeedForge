# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPECPATH)
tools_dir = project_root / "src" / "feedback_converter" / "tools"
data_dir = project_root / "src" / "feedback_converter" / "data"

# Native codec tools are optional and platform-specific. Include whichever
# binaries the build job supplied; runtime code also searches PATH.
binary_paths = [
    tools_dir / "ww2ogg.exe",
    tools_dir / "vgmstream-cli.exe",
    *tools_dir.glob("*.dll"),
]
binaries = [(str(item), "feedback_converter/tools") for item in binary_paths if item.is_file()]
datas = [
    (str(tools_dir / "packed_codebooks.bin"), "feedback_converter/tools"),
    (str(tools_dir / "packed_codebooks_aoTuV_603.bin"), "feedback_converter/tools"),
    (str(data_dir / "equipment.json"), "feedback_converter/data"),
    (str(data_dir / "feedback_equipment.json"), "feedback_converter/data"),
]

a = Analysis(
    [str(project_root / "src" / "feedback_converter" / "cli.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="psarc2feedpak",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="psarc2feedpak",
)
