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


AUTOMIX_PRESETS = ("auto", "smooth", "energetic", "dj")
"""Listening presets a project can pick (``ProjectSettings.automix_preset``).
Each is only a different set of the numbers above for the one planner: the UI
never passes raw values, and Preview and Export resolve the same preset."""

_PRESET_SETTINGS = {
    # The tuned defaults.
    "auto": AutoMixTransitionSettings(enabled=True),
    # Long, gentle blends; tempo is left alone unless tracks are already close,
    # and tracks that cannot be matched get a longer fade.
    "smooth": AutoMixTransitionSettings(
        enabled=True, preferred_bars=16, max_tempo_change_percent=6.0,
        max_transition_seconds=32.0, fallback_crossfade_seconds=5.0,
    ),
    # Short, punchy changes that keep the energy moving.
    "energetic": AutoMixTransitionSettings(
        enabled=True, preferred_bars=4, max_transition_seconds=12.0, fallback_crossfade_seconds=2.0,
    ),
    # Beat-matches wider tempo gaps (a DJ's +-12 % pitch range) over long 16-bar blends.
    "dj": AutoMixTransitionSettings(
        enabled=True, preferred_bars=16, max_tempo_change_percent=12.0, max_transition_seconds=32.0,
    ),
}


def resolve_automix_settings(preset: str | None) -> AutoMixTransitionSettings:
    """The immutable planner settings for ``preset`` (unknown or missing: "auto")."""
    return _PRESET_SETTINGS.get(preset or "auto", _PRESET_SETTINGS["auto"])
