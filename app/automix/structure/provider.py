"""The interface every structure analysis backend implements.

Mirrors ``app.automix.analysis.provider.AnalysisProvider``'s shape for the
same reason -- a pluggable, replaceable analyzer boundary -- but stays a
separate Protocol rather than reusing that one: the two produce different
result types (``TrackAnalysis`` vs. ``TrackStructureAnalysis``) for
different, independently-optional responsibilities (rhythm/key/energy vs.
structural timeline; see ``app.automix.structure.models``'s module
docstring).
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from app.automix.structure.models import TrackStructureAnalysis
from app.models.playlist import PlaylistTrack


class StructureAnalysisCancelled(Exception):
    """Raised by a provider (or StructureAnalysisService) when cancelled mid-track."""


class StructureAnalysisProvider(Protocol):
    """A pluggable structure analyzer. ``provider_id``/``version`` key the cache."""

    provider_id: str
    version: str

    def analyze(
        self,
        track: PlaylistTrack,
        *,
        cancel_event: threading.Event,
        progress: Callable[[float, str], None] | None = None,
    ) -> TrackStructureAnalysis:
        """Analyze one track, raising StructureAnalysisCancelled if ``cancel_event`` fires."""
        ...
