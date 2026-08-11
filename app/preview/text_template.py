"""Track-aware Text Source template expansion for video export."""

from __future__ import annotations

import re

from app.models.playlist import PlaylistTrack


def expand_track_template(template: str, track: PlaylistTrack, track_number: int,
                          track_total: int, start_seconds: float,
                          track_elapsed_seconds: float = 0.0,
                          playlist_duration_seconds: float | None = None) -> str:
    """Replace track and whole-video time tokens while preserving unknown tokens."""
    video_current_seconds = max(0.0, start_seconds + track_elapsed_seconds)
    video_total_seconds = (
        max(video_current_seconds, playlist_duration_seconds)
        if playlist_duration_seconds is not None else video_current_seconds
    )
    values = {
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "track": str(track_number),
        "track_total": str(track_total),
        "filename": track.filename,
        # Legacy aliases intentionally now follow the current song, which is
        # the expected meaning beside the current song's total duration.
        "current_time": format_timestamp(track_elapsed_seconds),
        "total_time": format_timestamp(track.duration_seconds),
        "track_current_time": format_timestamp(track_elapsed_seconds),
        "track_total_time": format_timestamp(track.duration_seconds),
        "video_current_time": format_timestamp(video_current_seconds),
        "video_total_time": format_timestamp(video_total_seconds),
    }
    return re.sub(
        r"%([a-z_]+)%",
        lambda match: values.get(match.group(1).lower(), match.group(0)),
        template,
    )


def format_timestamp(seconds: float) -> str:
    """Format a duration as MM:SS or HH:MM:SS for display templates."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes:02d}:{seconds_part:02d}"
