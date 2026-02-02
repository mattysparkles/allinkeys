# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPECPATH).resolve().parents[1]

datas = [
    (str(project_root / "alerts" / "sounds"), "alerts/sounds"),
    (str(project_root / "bin"), "bin"),
    (str(project_root / "keygen"), "keygen"),
    (str(project_root / "locale"), "locale"),
    (str(project_root / "plugins"), "plugins"),
    (str(project_root / "premium"), "premium"),
    (str(project_root / "config.txt"), "."),
    (str(project_root / "LICENSE.md"), "."),
    (str(project_root / "readme.md"), "."),
]

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AllInKeys",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AllInKeys",
)
