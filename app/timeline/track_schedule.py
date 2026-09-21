"""Central playlist track scheduling.

The sequential-timeline recurrence -- a track starts where the previous one
ends, unless its own ``start_time_seconds`` pushes it later -- used to be
re-derived inline in about a dozen places (playlist service, FFmpeg audio
assembly, export sampling, the export preview dialog, playlist text export).
Any one of those could drift from the others. This module is the single
place that computes it; every caller resolves through it instead.

No Qt or FFmpeg imports here on purpose: this sits below the UI, renderer,
and service layers so all of them can depend on it without a cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.playlist import PlaylistTrack


@dataclass(slots=True, frozen=True)
class TrackWindow:
    """One track's resolved position in the sequential playlist timeline."""

    track: PlaylistTrack
    floor: float
    """The earliest this track could start: the previous track's end."""
    start: float
    """The actual effective start (>= floor; later if start_time_seconds pushes it)."""
    end: float
    """start + track.duration_seconds."""


def resolve_track_windows(tracks: Sequence[PlaylistTrack]) -> list[TrackWindow]:
    """Resolve sequential timeline positions for tracks in playlist order."""
    cursor = 0.0
    windows: list[TrackWindow] = []
    for track in tracks:
        floor = cursor
        requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
        start = max(cursor, requested)
        end = start + track.duration_seconds
        windows.append(TrackWindow(track, floor, start, end))
        cursor = end
    return windows


def playlist_duration(tracks: Sequence[PlaylistTrack]) -> float:
    """Return the full timeline duration, including any user-created gaps."""
    windows = resolve_track_windows(tracks)
    return windows[-1].end if windows else 0.0
