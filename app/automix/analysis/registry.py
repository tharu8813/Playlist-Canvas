"""Selects an AnalysisProvider by id.

Both real AutoMix analysis call sites -- the interactive playlist-badge
analysis (app/controllers/automix_analysis_controller.py) and Preview/
Export rendering (app/renderer/ffmpeg_renderer.py) -- route through
``create_analysis_provider("auto", ...)``, so they can never resolve to a
different analyzer for the same playlist.

"auto" is always the light basic analyzer (librosa), even when PyTorch and
Beat This! happen to be installed: the PyTorch analyzers (Beat This!
downbeats, Demucs vocals) made the installer ~1 GB and pinned the CPU for
minutes on a first analysis, so they are not shipped and not used by
default. They stay available by explicit id ("beat_this") for tooling such
as tools/automix_listening_report.py.

Imports stay lazy and per-branch: constructing a "beat_this" provider never
imports torch/beat_this itself (BeatThisAnalysisProvider only does that
inside its own analyze() call).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.automix.analysis.provider import AnalysisProvider

BASIC_PROVIDER_ID = "basic"
BEAT_THIS_PROVIDER_ID = "beat_this"
AUTO_PROVIDER_ID = "auto"


def create_analysis_provider(provider_id: str, ffmpeg_executable: Path) -> AnalysisProvider:
    """Construct the AnalysisProvider named by ``provider_id``.

    Raises ValueError for anything other than "basic", "beat_this", or
    "auto" -- an unrecognized id is far more likely a settings typo or a
    wiring bug than an intentional choice, and silently degrading to
    "basic" would hide it (roadmap: "설정 typo나 wiring bug를 숨길 수 있다").
    """
    if provider_id == AUTO_PROVIDER_ID:
        provider_id = BASIC_PROVIDER_ID
    if provider_id == BEAT_THIS_PROVIDER_ID:
        from app.automix.analysis.beat_this import BeatThisAnalysisProvider
        return BeatThisAnalysisProvider(ffmpeg_executable)
    if provider_id == BASIC_PROVIDER_ID:
        from app.automix.analysis.basic import BasicAnalysisProvider
        return BasicAnalysisProvider(ffmpeg_executable)
    raise ValueError(f"Unknown AutoMix analysis provider_id: {provider_id!r}")


def beat_this_available() -> bool:
    """Whether the optional Beat This! engine could be imported (explicit use only).

    Uses importlib.util.find_spec, which checks installed packages without
    importing them (so it never loads torch's runtime).
    """
    return (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("beat_this") is not None
    )
