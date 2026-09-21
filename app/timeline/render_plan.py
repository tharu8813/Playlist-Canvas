"""Compiled, immutable execution plan for a Timeline.

Timeline is the editing model: what the user placed. CompiledRenderPlan is
the execution model: exactly how a compiler resolved that Timeline into
audio placement, visual ownership, and chapter metadata. Preview and Export
both read the same CompiledRenderPlan so they can never compute timing
differently from each other.

No Qt, no FFmpeg, no subprocess, no file I/O here on purpose -- this sits
below the renderer and UI layers so both can depend on it without a cycle.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from app.timeline.models import TransitionType


@dataclass(frozen=True, slots=True)
class AudioRenderClip:
    """One resolved audio placement: a clip, exactly where and how it plays."""

    clip_id: str
    track_id: str
    timeline_start: float
    source_in: float
    source_out: float
    playback_rate: float = 1.0
    gain: float = 1.0

    @property
    def duration(self) -> float:
        return (self.source_out - self.source_in) / self.playback_rate

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.duration


@dataclass(frozen=True, slots=True)
class AudioRenderTransition:
    """A resolved transition window; DSP for non-CUT types is a later phase."""

    clip_a: str
    clip_b: str
    timeline_start: float
    duration: float
    type: TransitionType = TransitionType.CUT


@dataclass(frozen=True, slots=True)
class AudioRenderPlan:
    """What actually gets mixed: resolved clip placements and transitions."""

    clips: tuple[AudioRenderClip, ...] = ()
    transitions: tuple[AudioRenderTransition, ...] = ()


@dataclass(frozen=True, slots=True)
class PresentationWindow:
    """The span during which one track owns what Canvas shows, even if audio overlaps."""

    track_id: str
    timeline_start: float
    timeline_end: float
    source_time_at_start: float = 0.0
    playback_rate: float = 1.0


@dataclass(frozen=True, slots=True)
class PresentationPlan:
    """Global timeline seconds -> visual owner. The one place that mapping lives.

    Audio active and presentation owner are different questions: AutoMix can
    play two clips at once while Canvas still shows a single track. Windows
    are assumed sorted and non-overlapping (the sequential compiler
    guarantees this; a future AutoMix planner must too).
    """

    windows: tuple[PresentationWindow, ...] = ()

    def _window_at(self, global_seconds: float) -> PresentationWindow | None:
        """The window owning this instant: before the first window it is the
        first window; in a gap between windows it is the one that most
        recently ended; after the last window it is the last window.
        """
        if not self.windows:
            return None
        starts = [window.timeline_start for window in self.windows]
        index = max(0, bisect_right(starts, global_seconds) - 1)
        return self.windows[index]

    def track_at(self, global_seconds: float) -> str | None:
        """The presentation owner's track_id at this global time, or None if empty."""
        window = self._window_at(global_seconds)
        return window.track_id if window else None

    def local_time(self, global_seconds: float) -> float | None:
        """The owning track's own elapsed seconds at this global time.

        Clamped to the window's own span so a query before its start or past
        its end (including a gap after it, or past the last window) holds
        steady at that boundary instead of drifting with global time.
        """
        window = self._window_at(global_seconds)
        if window is None:
            return None
        clamped = min(max(global_seconds, window.timeline_start), window.timeline_end)
        return window.source_time_at_start + (clamped - window.timeline_start) * window.playback_rate


@dataclass(frozen=True, slots=True)
class MetadataChapter:
    """One chapter/timestamp entry: a track's presentation span."""

    track_id: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class MetadataPlan:
    """Chapter markers and YouTube timestamps, derived from presentation ownership."""

    chapters: tuple[MetadataChapter, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledRenderPlan:
    """A Timeline, fully resolved into what Preview/Export actually need.

    Pure data: no Qt, no FFmpeg, no UI, no subprocess, no file writes, and
    never mutates the Timeline it was compiled from.
    """

    audio: AudioRenderPlan
    presentation: PresentationPlan
    metadata: MetadataPlan
    duration_seconds: float
