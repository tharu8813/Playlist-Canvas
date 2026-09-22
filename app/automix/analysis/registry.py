"""Selects an AnalysisProvider by id.

Both AutoMix analysis call sites (app/controllers/automix_analysis_controller.py
and app/renderer/ffmpeg_renderer.py) used to hardcode
``BasicAnalysisProvider(...)`` directly; this is now the one place that
decision is made, so a future settings toggle has a single point to plug
into instead of two. Imports stay lazy and per-branch, matching the
lazy-import convention already used at both call sites -- constructing a
"beat_this" provider never imports torch/beat_this itself (BeatThisAnalysisProvider
only does that inside its own analyze() call), so this function itself
never raises for a missing optional dependency.
"""

from __future__ import annotations

from pathlib import Path

from app.automix.analysis.provider import AnalysisProvider

BASIC_PROVIDER_ID = "basic"
BEAT_THIS_PROVIDER_ID = "beat_this"


def create_analysis_provider(provider_id: str, ffmpeg_executable: Path) -> AnalysisProvider:
    """Construct the AnalysisProvider named by ``provider_id`` (defaults to basic)."""
    if provider_id == BEAT_THIS_PROVIDER_ID:
        from app.automix.analysis.beat_this import BeatThisAnalysisProvider
        return BeatThisAnalysisProvider(ffmpeg_executable)
    from app.automix.analysis.basic import BasicAnalysisProvider
    return BasicAnalysisProvider(ffmpeg_executable)
