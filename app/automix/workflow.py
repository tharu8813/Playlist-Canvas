"""Ties analysis (Phase 1/2), planning (Phase 4), and UI status together.

Core rule (roadmap Phase 6 section 1): the UI never computes BPM, overlap,
or cues itself -- it only ever calls into this module and reads the
CompiledRenderPlan that comes back, exactly like Export will. Analysis and
planning are kept as two separate, independently callable steps (section
11): re-running ``analyze()`` is only needed when tracks or the analyzer
change (AnalysisService's own file-fingerprint cache already skips
unchanged tracks for free); re-running ``plan()`` alone is enough when
only a transition setting (bars, tempo budget, style) changed, since
AutoMix analysis data never depends on those settings.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from app.automix.analysis.provider import AnalysisProvider
from app.automix.analysis.service import AnalysisBatchResult, AnalysisService, ProgressCallback
from app.automix.cache import AnalysisCache
from app.automix.models import TrackAnalysis
from app.automix.planner import compile_automix
from app.automix.settings import AutoMixAnalysisSettings, AutoMixTransitionSettings
from app.models.playlist import PlaylistTrack
from app.timeline.render_plan import CompiledRenderPlan


@dataclass(frozen=True, slots=True)
class AutoMixWorkflowResult:
    """What a UI needs after one full analyze-then-plan pass."""

    plan: CompiledRenderPlan
    analyses: dict[str, TrackAnalysis]
    failures: dict[str, str]


class AutoMixWorkflow:
    """The one place a UI (or Export) goes to turn tracks into a CompiledRenderPlan."""

    def __init__(
        self, provider: AnalysisProvider, *,
        cache: AnalysisCache | None = None,
        analysis_settings: AutoMixAnalysisSettings | None = None,
    ) -> None:
        self.analysis_service = AnalysisService(provider, cache=cache, settings=analysis_settings)

    def analyze(
        self, tracks: Sequence[PlaylistTrack], *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> AnalysisBatchResult:
        """Analyze ``tracks``, reusing cached results for any unchanged file."""
        return self.analysis_service.analyze_tracks(tracks, cancel_event=cancel_event, progress=progress)

    def plan(
        self, tracks: Sequence[PlaylistTrack], analyses: dict[str, TrackAnalysis],
        transition_settings: AutoMixTransitionSettings,
    ) -> CompiledRenderPlan:
        """Compile a CompiledRenderPlan from already-known analyses and settings.

        Cheap and analysis-free: safe to call every time the user changes
        a transition setting without re-touching AnalysisService at all.
        """
        return compile_automix(tracks, analyses, transition_settings)

    def build(
        self, tracks: Sequence[PlaylistTrack], transition_settings: AutoMixTransitionSettings, *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> AutoMixWorkflowResult:
        """Analyze then plan in one call, for a caller that does not need the split."""
        analysis_result = self.analyze(tracks, cancel_event=cancel_event, progress=progress)
        plan = self.plan(tracks, analysis_result.analyses, transition_settings)
        return AutoMixWorkflowResult(
            plan=plan, analyses=analysis_result.analyses, failures=analysis_result.failures,
        )
