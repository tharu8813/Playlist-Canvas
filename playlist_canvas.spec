# -*- mode: python ; coding: utf-8 -*-
"""One-folder Windows build specification for Playlist Canvas."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH)
numpy_datas, numpy_binaries, numpy_hiddenimports = collect_all("numpy")
# AutoMix's default analyzer (app/automix/analysis/basic.py) needs librosa
# and the native/JIT pieces of its dependency chain: numba's LLVM JIT
# (llvmlite), scipy's compiled extensions, scikit-learn, and soundfile's
# bundled libsndfile. collect_all mirrors the existing numpy approach above
# so PyInstaller finds their binaries/data even where a hooks-contrib entry
# does not already cover them. NOT build-tested in this change -- verify a
# packaged build launches AutoMix analysis before shipping a release (see
# docs Phase 2 report's "Known Limitations").
automix_datas: list = []
automix_binaries: list = []
automix_hiddenimports: list = []
for _package in ("librosa", "numba", "llvmlite", "scipy", "sklearn", "soundfile"):
    _datas, _binaries, _hiddenimports = collect_all(_package)
    automix_datas += _datas
    automix_binaries += _binaries
    automix_hiddenimports += _hiddenimports

analysis = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=numpy_binaries + automix_binaries,
    # FFmpeg is deliberately not bundled. The installed application downloads
    # and checksum-verifies its own per-user copy on first use.
    datas=numpy_datas + automix_datas + [
        (str(project_root / "app" / "ui" / "studio.qss"), "app/ui"),
        (str(project_root / "app" / "assets" / "icons" / "check.svg"), "assets/icons"),
        (str(project_root / "app" / "resources" / "app_icon.ico"), "app/resources"),
        (str(project_root / "app" / "resources" / "language-pack-template.json"), "app/resources"),
        (str(project_root / "app" / "resources" / "ko.json"), "app/resources"),
        (str(project_root / "app" / "resources" / "en.json"), "app/resources"),
        (str(project_root / "app" / "assets" / "icons" / "spin_down.svg"), "assets/icons"),
        (str(project_root / "app" / "assets" / "icons" / "spin_up.svg"), "assets/icons"),
        (str(project_root / "LICENSE.txt"), "."),
    ],
    hiddenimports=numpy_hiddenimports + automix_hiddenimports + [
        "PySide6.QtSvg", "PySide6.QtMultimedia", "PySide6.QtOpenGLWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
# Qt uses Windows' system ICU. An unrelated Poppler installation on the build
# host can shadow it with a same-name DLL whose exports are incompatible.
analysis.binaries = [
    entry for entry in analysis.binaries
    if not (Path(entry[0]).name.lower() == "icuuc.dll" and "poppler" in entry[1].lower())
]
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Playlist Canvas",
    icon=str(project_root / "app" / "resources" / "app_icon.ico"),
    version=str(project_root / "windows_version_info.txt"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
COLLECT(
    executable,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    name="Playlist Canvas",
)
