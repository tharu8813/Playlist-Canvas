"""Selects an AnalysisProvider by id.

Both real AutoMix analysis call sites -- the interactive playlist-badge
analysis (app/controllers/automix_analysis_controller.py) and Preview/
Export rendering (app/renderer/ffmpeg_renderer.py) -- route through
``create_analysis_provider("auto", ...)``, so they can never resolve to a
different analyzer for the same playlist. "auto" picks Beat This! when its
optional dependency is actually importable, otherwise the always-available
basic analyzer -- BeatThisAnalysisProvider's own per-track fallback then
covers a later, track-specific failure (a corrupted model, one unreadable
file, ...) that this cheap up-front check cannot see.

Imports stay lazy and per-branch: constructing a "beat_this" provider never
imports torch/beat_this itself (BeatThisAnalysisProvider only does that
inside its own analyze() call), and the "auto" availability probe uses
importlib.util.find_spec, which locates a module without importing/
executing it -- so selecting a provider here never eagerly loads torch.
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
    Callers that want "unavailable/unknown -> basic" as deliberate
    behavior should pass "auto", not rely on this function's default.
    """
    if provider_id == AUTO_PROVIDER_ID:
        provider_id = BEAT_THIS_PROVIDER_ID if beat_this_available() else BASIC_PROVIDER_ID
    if provider_id == BEAT_THIS_PROVIDER_ID:
        from app.automix.analysis.beat_this import BeatThisAnalysisProvider
        return BeatThisAnalysisProvider(ffmpeg_executable)
    if provider_id == BASIC_PROVIDER_ID:
        from app.automix.analysis.basic import BasicAnalysisProvider
        return BasicAnalysisProvider(ffmpeg_executable)
    raise ValueError(f"Unknown AutoMix analysis provider_id: {provider_id!r}")


def beat_this_available() -> bool:
    """Cheap up-front availability probe for the optional Beat This! engine.

    Uses importlib.util.find_spec, which resolves whether a module *could*
    be imported by checking installed package metadata/finders, without
    actually importing (and therefore without loading torch's runtime).
    A module that is findable but broken (corrupted install, incompatible
    binary, ...) still fails at actual import time inside
    BeatThisAnalysisProvider._load_model(), which is caught there and
    degrades to a basic-analyzer result for that track -- this probe only
    decides the up-front "auto" choice, it is not the only safety net.
    """
    return (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("beat_this") is not None
    )
