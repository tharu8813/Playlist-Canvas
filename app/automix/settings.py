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
