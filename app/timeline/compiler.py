"""Compile a Timeline into a CompiledRenderPlan.

This is the Sequential planner: it reproduces the legacy playlist schedule
exactly (via timeline_from_playlist -> resolve_track_windows) and is the
only planner implemented today. A future AutoMixPlanner or
ManualTimelinePlanner would live beside this module and produce the same
CompiledRenderPlan shape, so Preview/Export never need to know which
planner ran.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.playlist import PlaylistTrack
from app.timeline.models import Timeline, timeline_from_playlist
from app.timeline.render_plan import (
    AudioRenderClip,
    AudioRenderPlan,
    CompiledRenderPlan,
    MetadataChapter,
    MetadataPlan,
    PresentationPlan,
    PresentationWindow,
)


@dataclass(frozen=True, slots=True)
class TimelineCompileOptions:
    """Reserved for future planner selection and AutoMix parameters.

    Empty today on purpose: no AutoMix config exists yet. Keeping this as a
    dedicated options object (rather than adding kwargs later) means
    compile_timeline's signature won't need to change when that config
    lands.
    """


def compile_timeline(
    timeline: Timeline,
    options: TimelineCompileOptions | None = None,
) -> CompiledRenderPlan:
    """Resolve a Timeline snapshot into an immutable CompiledRenderPlan.

    Clips disabled on the Timeline are excluded from the compiled plan (they
    are never rendered or presented), while ``duration_seconds`` mirrors
    Timeline.duration, which counts them -- same as the editor already does.
    """
    clips = tuple(
        AudioRenderClip(
            clip_id=clip.id,
            track_id=clip.track_id,
            timeline_start=clip.timeline_start,
            source_in=clip.source_in,
            source_out=clip.source_out,
            playback_rate=clip.playback_rate,
            gain=clip.gain,
        )
        for track in timeline.audio_tracks
        for clip in track.clips
        if clip.enabled
    )
    windows = tuple(
        PresentationWindow(
            track_id=clip.track_id,
            timeline_start=clip.timeline_start,
            timeline_end=clip.timeline_end,
        )
        for clip in clips
    )
    chapters = tuple(
        MetadataChapter(track_id=window.track_id, start=window.timeline_start, end=window.timeline_end)
        for window in windows
    )
    return CompiledRenderPlan(
        audio=AudioRenderPlan(clips=clips),
        presentation=PresentationPlan(windows=windows),
        metadata=MetadataPlan(chapters=chapters),
        duration_seconds=timeline.duration,
    )


def compile_playlist(
    tracks: Sequence[PlaylistTrack], *, enabled_only: bool = False,
) -> CompiledRenderPlan:
    """Convenience path straight from a legacy playlist to a CompiledRenderPlan."""
    return compile_timeline(timeline_from_playlist(tracks, enabled_only=enabled_only))
