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
# does not already cover them.
#
# The installer also ships AutoMix's advanced analyzers: Beat This! (beat and
# downbeat model on CPU PyTorch) and Sonara (song structure). torch itself is
# collected by pyinstaller-hooks-contrib's hook; the Beat This checkpoint is
# staged below so the installed app analyzes offline.
#
# collect_all only *warns* for a package that is not installed, which is how
# 1.2.0.6 shipped without librosa and silently lost AutoMix. A release build
# must fail instead.
import importlib.util
import shutil

AUTOMIX_PACKAGES = (
    "librosa", "numba", "llvmlite", "scipy", "sklearn", "soundfile",
    "torch", "torchaudio", "beat_this", "einops", "rotary_embedding_torch", "soxr", "sonara",
)
_missing = [name for name in AUTOMIX_PACKAGES if importlib.util.find_spec(name) is None]
if _missing:
    raise SystemExit(
        "Release build environment is missing AutoMix packages: " + ", ".join(_missing)
        + ". Install requirements-lock.txt and requirements-packaging.txt (see PACKAGING.md)."
    )

automix_datas: list = []
automix_binaries: list = []
automix_hiddenimports: list = []
for _package in AUTOMIX_PACKAGES:
    if _package == "torch":
        continue  # the contrib hook collects torch; collect_all would add its test suites
    _datas, _binaries, _hiddenimports = collect_all(_package)
    automix_datas += _datas
    automix_binaries += _binaries
    automix_hiddenimports += _hiddenimports

from beat_this.inference import load_checkpoint  # noqa: E402
import torch  # noqa: E402

_checkpoint_cache = Path(torch.hub.get_dir()) / "checkpoints" / "beat_this-final0.ckpt"
if not _checkpoint_cache.is_file():
    load_checkpoint("final0")  # one-time download into torch's hub cache
_checkpoint_stage = project_root / "build" / "beat_this_checkpoints"
_checkpoint_stage.mkdir(parents=True, exist_ok=True)
shutil.copyfile(_checkpoint_cache, _checkpoint_stage / "final0.ckpt")
# Must match app.automix.analysis.beat_this.BUNDLED_CHECKPOINT_DIRECTORY.
automix_datas.append((str(_checkpoint_stage / "final0.ckpt"), "beat_this/checkpoints"))

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
