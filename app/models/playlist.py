"""Serializable playlist track model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from math import isfinite
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class PlaylistTrack:
    """Audio metadata and inclusion state for a single playlist entry."""

    file_path: str
    title: str
    artist: str = "Unknown Artist"
    album: str = "Unknown Album"
    duration_seconds: float = 0.0
    start_time_seconds: float | None = None
    enabled: bool = True
    lyrics_path: str = ""
    lyrics: list[dict[str, Any]] = field(default_factory=list)
    lyrics_timing_offset_seconds: float = 0.0
    id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def filename(self) -> str:
        """Return only the user-facing source file name."""
        return Path(self.file_path).name

    @property
    def duration_label(self) -> str:
        """Format duration as minutes and seconds."""
        total_seconds = max(0, round(self.duration_seconds))
        return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"

    def to_dict(self) -> dict[str, Any]:
        """Convert the model to JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlaylistTrack":
        """Restore a playlist track from JSON-compatible data."""
        if not isinstance(data, dict):
            raise ValueError("Project playlist tracks must be objects.")
        track = cls(**data)
        if not isinstance(track.id, str) or not track.id.strip():
            raise ValueError("Every playlist track must have a non-empty string ID.")
        if not isinstance(track.file_path, str) or not isinstance(track.title, str):
            raise ValueError("Playlist file paths and titles must be strings.")
        duration = track.duration_seconds
        if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                or not isfinite(float(duration)) or duration < 0):
            raise ValueError(f"Track '{track.title}' has an invalid duration.")
        if track.start_time_seconds is not None:
            start = track.start_time_seconds
            if (not isinstance(start, (int, float)) or isinstance(start, bool)
                    or not isfinite(float(start)) or start < 0):
                raise ValueError(f"Track '{track.title}' has an invalid start time.")
        if not isinstance(track.enabled, bool) or not isinstance(track.lyrics, list):
            raise ValueError(f"Track '{track.title}' has invalid enabled or lyrics data.")
        offset = track.lyrics_timing_offset_seconds
        if (not isinstance(offset, (int, float)) or isinstance(offset, bool)
                or not isfinite(float(offset)) or abs(float(offset)) > 3_600):
            raise ValueError(f"Track '{track.title}' has an invalid lyric timing offset.")
        for cue in track.lyrics:
            if not isinstance(cue, dict) or not isinstance(cue.get("text", ""), str):
                raise ValueError(f"Track '{track.title}' contains an invalid lyric cue.")
            for key in ("start", "end"):
                value = cue.get(key)
                if (not isinstance(value, (int, float)) or isinstance(value, bool)
                        or not isfinite(float(value)) or value < 0):
                    raise ValueError(
                        f"Track '{track.title}' lyric cue has an invalid '{key}' value."
                    )
            if float(cue["end"]) < float(cue["start"]):
                raise ValueError(f"Track '{track.title}' lyric cue ends before it starts.")
            cue["text"] = str(cue.get("text", "")).replace("\\n", "\n")
        track.lyrics.sort(key=lambda cue: (float(cue["start"]), float(cue["end"])))
        return track
