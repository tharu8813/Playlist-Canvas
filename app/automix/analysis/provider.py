"""The interface every AutoMix analysis backend implements.

Phase 1 defines this boundary only. Phase 2 supplies the first real
implementation (BPM/beat/downbeat); Phase 7 may add optional advanced
providers behind the same shape.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from app.automix.models import TrackAnalysis
from app.models.playlist import PlaylistTrack


class AnalysisCancelled(Exception):
    """Raised by a provider (or AnalysisService) when cancelled mid-track."""


class AnalysisProvider(Protocol):
    """A pluggable audio analyzer. ``provider_id``/``version`` key the cache."""

    provider_id: str
    version: str

    def analyze(
        self,
        track: PlaylistTrack,
        *,
        cancel_event: threading.Event,
        progress: Callable[[float, str], None] | None = None,
    ) -> TrackAnalysis:
        """Analyze one track, raising AnalysisCancelled if ``cancel_event`` fires."""
        ...
