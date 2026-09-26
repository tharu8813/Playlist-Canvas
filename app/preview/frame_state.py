"""Frame Evaluation Layer: pure per-source state resolvers shared by the
live Canvas preview (CanvasSnapshot.capture_track) and export's frame
coalescing (ExportCanvasCapturer._source_state_key).

Both call sites used to independently re-derive the same timing math -- one
to paint it, one to build a cache key -- with a real risk of the two
drifting apart. resolve_lyrics_cue_state(), resolve_now_playing_exit_state(),
and resolve_timeline_window_phase() pull that duplicated computation out
into pure, testable functions; both call sites now resolve the same
FrameState value instead of duplicating the formula. Background cross-fade
blend and text template expansion were checked and found to already be
single-line/shared, not worth extracting.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.playlist import PlaylistTrack
from app.models.source import Source
from app.services.lyrics_service import LyricsService


@dataclass(slots=True, frozen=True)
class LyricsCueState:
    """Which lyric cue is current at a point in playback, and how far its
    entrance transition has progressed (raw, unEASED -- callers that need an
    eased value apply their own easing curve to transition_progress)."""

    cue_index: int | None
    active_cue_index: int | None
    transitioning: bool
    transition_progress: float
    cue_start_seconds: float | None


def resolve_lyrics_cue_state(
    track: PlaylistTrack,
    elapsed_seconds: float,
    lyrics_timing_offset_seconds: float,
    subtitle_timing_offset: float,
    subtitle_animation: str,
    subtitle_animation_duration: float,
) -> LyricsCueState:
    """Resolve the LYRICS source's cue/transition state at elapsed_seconds
    into a playing track. Pulled out of CanvasSnapshot.capture_track's inline
    lyrics branch verbatim; behavior is unchanged."""
    effective_offset = lyrics_timing_offset_seconds + subtitle_timing_offset
    lyric_elapsed = max(0.0, elapsed_seconds + effective_offset)
    active_cue_index = LyricsService.current_cue_index(track.lyrics, lyric_elapsed)
    cue_index = LyricsService.display_cue_index(track.lyrics, lyric_elapsed)
    lyric_cue = track.lyrics[cue_index] if cue_index is not None else None
    transitioning = (
        cue_index is not None
        and active_cue_index == cue_index
        and lyric_cue is not None
        and subtitle_animation != "none"
    )
    transition_progress = 1.0
    cue_start_seconds: float | None = None
    if transitioning:
        cue_start_seconds = float(lyric_cue.get("start", lyric_elapsed)) - effective_offset
        transition_progress = max(0.0, min(1.0, (
            (elapsed_seconds - cue_start_seconds) / max(0.05, subtitle_animation_duration)
        )))
    return LyricsCueState(
        cue_index=cue_index,
        active_cue_index=active_cue_index,
        transitioning=transitioning,
        transition_progress=transition_progress,
        cue_start_seconds=cue_start_seconds,
    )


@dataclass(slots=True, frozen=True)
class NowPlayingExitState:
    """Whether a NOW_PLAYING card is visible at all, and how far its exit
    animation has progressed (raw, unEASED -- callers apply their own easing
    curve to exit_progress for the actual motion/opacity)."""

    visible: bool
    exit_progress: float | None


def resolve_now_playing_exit_state(
    elapsed_seconds: float,
    now_playing_duration: float,
    now_playing_exit_duration: float,
) -> NowPlayingExitState:
    """Resolve the NOW_PLAYING source's visibility/exit-progress state at
    elapsed_seconds. Pulled out of CanvasSnapshot.capture_track's inline
    now-playing branch verbatim; behavior is unchanged."""
    visible = elapsed_seconds <= now_playing_duration
    if not visible:
        return NowPlayingExitState(visible=False, exit_progress=None)
    exit_duration = min(now_playing_exit_duration, now_playing_duration)
    exit_start = now_playing_duration - exit_duration
    exit_progress: float | None = None
    if elapsed_seconds >= exit_start and exit_duration > 0.0:
        exit_progress = max(0.0, min(1.0, (elapsed_seconds - exit_start) / exit_duration))
    return NowPlayingExitState(visible=True, exit_progress=exit_progress)


@dataclass(slots=True, frozen=True)
class MixJunction:
    """Where a frame sits relative to crossfade/AutoMix handovers of the Canvas.

    ``visual_elapsed``: seconds since this track took over the Canvas in the
    middle of an overlap (``None``: it started plainly). ``visual_remaining``:
    seconds until it hands over in the middle of the next overlap (``None``:
    it ends plainly). ``mix_in_seconds``/``mix_out_seconds``: the overlap
    after/before those handovers, which "fit to the mix" animations last.
    See render_plan.visual_segments.
    """

    visual_elapsed: float | None = None
    visual_remaining: float | None = None
    mix_in_seconds: float = 0.0
    mix_out_seconds: float = 0.0


def segment_junction(segment, global_seconds: float) -> MixJunction | None:
    """The MixJunction of a render_plan.VisualSegment at ``global_seconds``."""
    if segment is None or not (segment.mixed_in or segment.mixed_out):
        return None
    return MixJunction(
        global_seconds - segment.start if segment.mixed_in else None,
        segment.end - global_seconds if segment.mixed_out else None,
        segment.mix_in_seconds, segment.mix_out_seconds,
    )


def mix_animation(
    source: Source, phase: str, junction: MixJunction | None = None,
) -> tuple[str, float]:
    """(style, seconds) of a source's entrance ("in") or exit ("out") at a mix handover.

    The same styles as at a plain track change; with ``animation_fit_mix`` they
    last as long as the audio mix on that side of the handover.
    """
    style, duration, span = (
        (source.animation_in, source.animation_in_duration,
         junction.mix_in_seconds if junction else 0.0)
        if phase == "in" else
        (source.animation_out, source.animation_out_duration,
         junction.mix_out_seconds if junction else 0.0)
    )
    return style, (span if source.animation_fit_mix and span > 0.0 else duration)


def mix_phase_durations(sources, junction: MixJunction | None = None) -> tuple[float, float]:
    """The longest mix entrance and exit among ``sources`` (0.0 where none animates)."""
    durations = []
    for phase in ("in", "out"):
        durations.append(max(
            (duration for style, duration in (
                mix_animation(source, phase, junction) for source in sources)
             if style != "none"),
            default=0.0,
        ))
    return durations[0], durations[1]


def resolve_mix_phase(
    sources, junction: MixJunction | None,
) -> tuple[str | None, float, float]:
    """(phase, progress, phase seconds) of the whole frame at a mix handover, like
    the plain track-edge phase: an entrance right after it, an exit right before."""
    if junction is None:
        return None, 1.0, 0.0
    intro, outro = mix_phase_durations(sources, junction)
    if junction.visual_elapsed is not None and junction.visual_elapsed < intro:
        return "in", junction.visual_elapsed / intro, intro
    if junction.visual_remaining is not None and junction.visual_remaining < outro:
        return "out", 1.0 - junction.visual_remaining / outro, outro
    return None, 1.0, 0.0


def resolve_edge_animation(
    source: Source, phase: str | None, *,
    elapsed_seconds: float, track_duration: float,
    phase_progress: float = 1.0, phase_duration: float | None = None,
    junction: MixJunction | None = None,
) -> tuple[str, str, float] | None:
    """(phase, style, raw progress) of a source's entrance/exit at a track edge, or None.

    The one formula for Canvas capture and export's frame cache keys. At a mix
    handover every source times its entrance/exit from the handover itself (see
    mix_animation); elsewhere the plain track-start/end animation applies.
    """
    if junction is not None:
        for edge, seconds in (("in", junction.visual_elapsed), ("out", junction.visual_remaining)):
            if seconds is None:
                continue
            style, duration = mix_animation(source, edge, junction)
            if style == "none" or duration <= 0.0 or seconds >= duration:
                continue
            progress = seconds / duration if edge == "in" else 1.0 - seconds / duration
            return edge, style, max(0.0, min(1.0, progress))
        if phase in {"in", "out"} and (
            (phase == "in" and junction.visual_elapsed is not None)
            or (phase == "out" and junction.visual_remaining is not None)
        ):
            return None  # that edge is a mix handover: the plain animation never plays there
    if phase is None:
        return None
    style = source.animation_in if phase == "in" else source.animation_out
    if style == "none":
        return None
    if phase_duration is None:
        # Isolated callers that only provide normalized progress.
        return phase, style, max(0.0, min(1.0, phase_progress))
    configured = source.animation_in_duration if phase == "in" else source.animation_out_duration
    duration = max(0.001, min(configured, phase_duration))
    if phase == "in":
        raw = elapsed_seconds / duration
    else:
        raw = (elapsed_seconds - max(0.0, track_duration - duration)) / duration
    return phase, style, max(0.0, min(1.0, raw))


def resolve_timeline_window_phase(
    source: Source, global_seconds: float,
) -> tuple[str | None, float]:
    """Return the in/out phase for a source at its own timeline-window edge.

    Sources restricted to a portion of the playlist previously appeared and
    vanished with a hard cut. When they carry an entrance or exit style,
    animate them across that style's duration on either side of the window.
    Pulled out of CanvasSnapshot._timeline_window_phase verbatim; behavior is
    unchanged. ExportCanvasCapturer._source_state_key now calls this too, so
    a source with both a timeline window and an animation style gets a cache
    key that reflects the window animation instead of silently coalescing
    visually different frames.
    """
    start = source.timeline_start
    duration = source.timeline_duration
    if start <= 0.0 and duration <= 0.0:
        return None, 1.0
    in_duration = (
        source.animation_in_duration if source.animation_in != "none" else 0.0
    )
    out_duration = (
        source.animation_out_duration if source.animation_out != "none" else 0.0
    )
    if in_duration > 0.0 and start <= global_seconds < start + in_duration:
        return "in", (global_seconds - start) / in_duration
    if duration > 0.0 and out_duration > 0.0:
        end = start + duration
        if end - out_duration <= global_seconds < end:
            return "out", (global_seconds - (end - out_duration)) / out_duration
    return None, 1.0
