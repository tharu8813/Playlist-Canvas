"""Runtime settings for AutoMix analysis infrastructure.

Deliberately not wired into AppSettings/project JSON yet -- Phase 1 has no
AutoMix UI and no new persisted project schema (roadmap 1.2, 1.3).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AutoMixAnalysisSettings:
    """How AnalysisService should run, independent of which provider it uses."""

    provider_id: str = "basic"
    use_cache: bool = True
    max_workers: int | None = None


@dataclass(frozen=True, slots=True)
class AutoMixTransitionSettings:
    """Conservative defaults for transition candidate generation (Phase 3).

    ``enabled`` gates whether a planner should attempt AutoMix transitions
    at all -- kept here rather than as an implicit "settings object
    exists" convention, so a caller can hold one disabled settings
    instance around without it doing anything.
    """

    enabled: bool = False
    preferred_bars: int = 8
    max_tempo_change_percent: float = 8.0
    min_transition_seconds: float = 2.0
    max_transition_seconds: float = 20.0
    allow_half_double_tempo: bool = True
    fallback_crossfade_seconds: float = 3.0
