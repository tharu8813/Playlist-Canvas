"""Frame Evaluation Layer: pure per-source state resolvers shared by the
live Canvas preview (CanvasSnapshot.capture_track) and export's frame
coalescing (ExportCanvasCapturer._source_state_key).

Both call sites used to independently re-derive the same lyric cue/transition
timing math -- one to paint it, one to build a cache key -- with a real risk
of the two drifting apart. resolve_lyrics_cue_state() is the first slice of
that computation pulled out into one pure, testable function; both call
sites now resolve the same FrameState value instead of duplicating the
formula. Later slices can extract the other duplicated branches
(track template text, now-playing exit progress, background cross-fade,
animation in/out progress) the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.playlist import PlaylistTrack
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
