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
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite

from app.timeline.models import TransitionType


@dataclass(frozen=True, slots=True)
class TempoRamp:
    """A clip's tail tempo change: ``playback_rate`` until ``source_start``,
    ``end_rate`` from ``source_end`` on, and ``steps`` equal source slices
    stepping between them. AutoMix uses it to walk the outgoing track onto
    the incoming track's beat before their overlap; the renderer plays the
    exact same steps (atempo commands), so the timing here is what renders."""

    source_start: float
    source_end: float
    end_rate: float
    steps: int = 32


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
    tempo_ramp: TempoRamp | None = None

    def rate_segments(self) -> tuple[tuple[float, float, float], ...]:
        """``(source_start, source_end, rate)`` spans covering ``[source_in, source_out]``."""
        ramp = self.tempo_ramp
        if ramp is None:
            return ((self.source_in, self.source_out, self.playback_rate),)
        start = min(max(ramp.source_start, self.source_in), self.source_out)
        end = min(max(ramp.source_end, start), self.source_out)
        step = (end - start) / ramp.steps
        segments = [(self.source_in, start, self.playback_rate)]
        segments.extend(
            (start + index * step, start + (index + 1) * step,
             self.playback_rate + (ramp.end_rate - self.playback_rate) * (index + 0.5) / ramp.steps)
            for index in range(ramp.steps)
        )
        segments.append((end, self.source_out, ramp.end_rate))
        return tuple(segment for segment in segments if segment[1] > segment[0])

    def timeline_at(self, source_seconds: float) -> float:
        """Timeline second at which this clip plays ``source_seconds`` of its track."""
        position = self.timeline_start
        for start, end, rate in self.rate_segments():
            if source_seconds <= end:
                return position + (max(source_seconds, start) - start) / rate
            position += (end - start) / rate
        return position

    def source_at(self, timeline_seconds: float) -> float:
        """Track second this clip plays at ``timeline_seconds`` (clamped to the clip)."""
        position = self.timeline_start
        for start, end, rate in self.rate_segments():
            length = (end - start) / rate
            if timeline_seconds <= position + length:
                return start + max(0.0, timeline_seconds - position) * rate
            position += length
        return self.source_out

    @property
    def duration(self) -> float:
        if self.tempo_ramp is None:
            return (self.source_out - self.source_in) / self.playback_rate
        return sum((end - start) / rate for start, end, rate in self.rate_segments())

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.duration


class TransitionDsp(str, Enum):
    """How a transition's overlap is mixed, chosen by whoever built the plan.

    Separate from ``TransitionType`` (which also describes non-AutoMix
    timeline transitions): the type says what kind of junction this is, the
    DSP style says how the renderer mixes that exact window. Never changes
    timing -- every style renders the same ``duration``.
    """

    BASS_SWAP = "bass_swap"
    VOCAL_SAFE_EQ = "vocal_safe_eq"
    FILTER_BLEND = "filter_blend"
    SHORT_FADE = "short_fade"
    FILTER_SWEEP = "filter_sweep"


@dataclass(frozen=True, slots=True)
class AudioRenderTransition:
    """A resolved transition window and, optionally, how to mix it.

    ``dsp=None`` keeps the type's own rendering (EQUAL_POWER qsin, CROSSFADE
    tri, BEAT_MATCH bass swap), so plans that never set it are unchanged.
    ``dsp_reasons`` is runtime diagnostics from whoever chose ``dsp`` (the
    AutoMix selector): why that style, for tuning. Never read by the
    renderer, never persisted -- compiled plans are rebuilt, not saved.
    ``details`` is the same kind of diagnostics as (name, value) pairs --
    the planner's numbers (BPMs, rate, cues, score, energy/vocal/key facts)
    for Preview's AutoMix details and the diagnostics export.
    """

    clip_a: str
    clip_b: str
    timeline_start: float
    duration: float
    type: TransitionType = TransitionType.CUT
    dsp: TransitionDsp | None = None
    dsp_reasons: tuple[str, ...] = ()
    details: tuple[tuple[str, object], ...] = ()
    vocal_handoff: float | None = None
    """VOCAL_SAFE_EQ: where (0..1 of the window) the mid band hands over;
    ``None`` is the style's default. Chosen by the planner, rendered as is."""


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


def build_presentation_and_metadata(
    clips: Sequence[AudioRenderClip],
) -> tuple[PresentationPlan, MetadataPlan, float]:
    """Derive Presentation ownership, chapters, and duration from placed clips.

    Shared by every compiler (Sequential in app/timeline/compiler.py, and
    AutoMix in app/automix/planner.py) so gap and chapter semantics can
    never drift between them (roadmap "Timing invariant"). Presentation
    ownership switches exactly when each clip starts, regardless of
    whether an earlier clip's audio is still playing underneath it -- so
    windows stay non-overlapping even when AudioRenderPlan.clips overlap.
    Callers are responsible for passing clips already sorted by
    timeline_start.
    """
    windows = tuple(
        PresentationWindow(
            track_id=clip.track_id,
            timeline_start=clip.timeline_start,
            timeline_end=clip.timeline_end,
            source_time_at_start=clip.source_in,
            # ponytail: visuals ignore a tail TempoRamp (a few % over its last bars, only
            # until the next owner takes over); map through clip.source_at if lyrics need it.
            playback_rate=clip.playback_rate,
        )
        for clip in clips
    )
    duration = max((clip.timeline_end for clip in clips), default=0.0)
    chapters = tuple(
        MetadataChapter(
            track_id=window.track_id,
            start=window.timeline_start,
            end=windows[index + 1].timeline_start if index + 1 < len(windows) else duration,
        )
        for index, window in enumerate(windows)
    )
    return PresentationPlan(windows=windows), MetadataPlan(chapters=chapters), duration


def validate_compiled_render_plan(plan: CompiledRenderPlan) -> None:
    """Raise ValueError if ``plan`` violates an architecture invariant.

    Compilers are trusted to build correct plans; this exists as a cheap
    defensive check any compiler (Sequential or AutoMix) can call on its
    own output, and for tests, per roadmap Phase 4 section 14.
    """
    for clip in plan.audio.clips:
        if not isfinite(clip.timeline_start) or clip.timeline_start < 0.0:
            raise ValueError(f"Clip {clip.clip_id!r} has an invalid timeline_start.")
        if not isfinite(clip.source_in) or not isfinite(clip.source_out) or clip.source_out < clip.source_in:
            raise ValueError(f"Clip {clip.clip_id!r} has invalid source bounds.")
        if not isfinite(clip.playback_rate) or clip.playback_rate <= 0.0:
            raise ValueError(f"Clip {clip.clip_id!r} has an invalid playback_rate.")
        ramp = clip.tempo_ramp
        if ramp is not None and not (
            isfinite(ramp.end_rate) and ramp.end_rate > 0.0 and ramp.steps >= 1
            and clip.source_in <= ramp.source_start <= ramp.source_end <= clip.source_out
        ):
            raise ValueError(f"Clip {clip.clip_id!r} has an invalid tempo ramp.")

    clip_ids = {clip.clip_id for clip in plan.audio.clips}
    for transition in plan.audio.transitions:
        if transition.clip_a not in clip_ids or transition.clip_b not in clip_ids:
            raise ValueError("A transition references a clip that is not in the compiled plan.")
        if not isfinite(transition.duration) or transition.duration <= 0.0:
            raise ValueError("A transition must have a finite, positive duration.")

    # Ownership is resolved purely by each window's timeline_start (see
    # PresentationPlan._window_at) -- strictly increasing starts is exactly
    # what that lookup needs, regardless of whether the underlying audio
    # clips overlap (AutoMix) or not (Sequential). A window's own
    # timeline_end reflects its clip's actual audio span and may run past
    # the next window's start once AutoMix places two clips concurrently;
    # that is expected, not a violation.
    previous_start = float("-inf")
    for window in plan.presentation.windows:
        if window.timeline_start <= previous_start:
            raise ValueError("Presentation window starts must be strictly increasing.")
        previous_start = window.timeline_start

    previous_chapter_start = float("-inf")
    for chapter in plan.metadata.chapters:
        if chapter.start < previous_chapter_start:
            raise ValueError("Chapters must be ordered by start.")
        previous_chapter_start = chapter.start

    expected_duration = max((clip.timeline_end for clip in plan.audio.clips), default=0.0)
    if abs(plan.duration_seconds - expected_duration) > 1e-6:
        raise ValueError("duration_seconds must equal the furthest compiled clip end.")
