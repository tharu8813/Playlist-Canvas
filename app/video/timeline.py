"""Deterministic scheduling shared by video preview and export."""

from __future__ import annotations

from dataclasses import dataclass
import random
from collections.abc import Mapping, Sequence

from app.models.source import Source


@dataclass(frozen=True, slots=True)
class VideoFramePosition:
    """The media file and local timestamp visible at one timeline instant."""

    path: str
    seconds: float
    cycle: int
    item_index: int


@dataclass(frozen=True, slots=True)
class VideoClipOccurrence:
    """One continuous clip interval on the exported playlist timeline."""

    path: str
    timeline_start: float
    duration_seconds: float
    media_start_seconds: float = 0.0
    loop_media: bool = False


def resolve_video_position(
    source: Source,
    paths: Sequence[str],
    durations: Mapping[str, float],
    elapsed_seconds: float,
) -> VideoFramePosition | None:
    """Resolve a video source without relying on mutable playback state.

    A cycle is one pass through all selected clips. Random mode shuffles that
    pass with a stable source seed, so editor preview and export choose the
    same media at every timestamp.
    """
    usable = [
        (path, float(durations.get(path, 0.0)) / max(0.05, source.video_speed))
        for path in paths
        if path and float(durations.get(path, 0.0)) > 0.0
    ]
    if not usable or elapsed_seconds < 0.0:
        return None
    mode = source.video_repeat_mode
    if mode in {"once", "loop_one"}:
        usable = usable[:1]
    if mode == "once":
        path, duration = usable[0]
        if elapsed_seconds >= duration:
            return None
        return VideoFramePosition(path, elapsed_seconds * source.video_speed, 0, 0)
    if mode == "loop_one":
        path, duration = usable[0]
        return VideoFramePosition(
            path, (elapsed_seconds % duration) * source.video_speed,
            int(elapsed_seconds // duration), 0,
        )

    cycle_duration = sum(duration for _path, duration in usable)
    if cycle_duration <= 0.0:
        return None
    cycle = int(elapsed_seconds // cycle_duration)
    if not source.video_cycle_unlimited and cycle >= source.video_cycle_count:
        return None
    within = elapsed_seconds - cycle * cycle_duration
    ordered = list(enumerate(usable))
    if mode == "random":
        random.Random(source.video_random_seed + cycle).shuffle(ordered)
    for original_index, (path, duration) in ordered:
        if within < duration:
            return VideoFramePosition(
                path, within * source.video_speed, cycle, original_index,
            )
        within -= duration
    return None


def source_video_paths(source: Source, track_video_paths: Sequence[str]) -> list[str]:
    """Select the per-track or whole-timeline media list for a source."""
    return list(track_video_paths if source.video_timing_mode == "track" else source.video_paths)


def build_video_occurrences(
    source: Source,
    paths: Sequence[str],
    durations: Mapping[str, float],
    timeline_start: float,
    available_duration: float,
) -> list[VideoClipOccurrence]:
    """Expand repeat rules into bounded, non-overlapping export intervals."""
    if available_duration <= 0.0:
        return []
    usable = [path for path in paths if float(durations.get(path, 0.0)) > 0.0]
    if source.video_repeat_mode in {"once", "loop_one"}:
        usable = usable[:1]
    if not usable:
        return []
    if source.video_repeat_mode == "loop_one":
        return [VideoClipOccurrence(
            usable[0], timeline_start, available_duration, 0.0, True,
        )]
    result: list[VideoClipOccurrence] = []
    cursor = 0.0
    cycle = 0
    while cursor < available_duration - 1e-6:
        if source.video_repeat_mode == "once" and cycle > 0:
            break
        if (source.video_repeat_mode in {"sequence", "random"}
                and not source.video_cycle_unlimited
                and cycle >= source.video_cycle_count):
            break
        ordered = list(usable)
        if source.video_repeat_mode == "random":
            random.Random(source.video_random_seed + cycle).shuffle(ordered)
        for path in ordered:
            output_duration = float(durations[path]) / max(0.05, source.video_speed)
            duration = min(output_duration, available_duration - cursor)
            if duration <= 1e-6:
                break
            result.append(VideoClipOccurrence(
                path, timeline_start + cursor, duration,
            ))
            cursor += duration
            if cursor >= available_duration - 1e-6:
                break
            if source.video_repeat_mode == "once":
                break
        cycle += 1
        if source.video_repeat_mode == "once":
            break
    return result
