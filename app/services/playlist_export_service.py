"""Generate YouTube description and playlist CSV companion files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from app.models.playlist import PlaylistTrack


class TimestampFormat(str, Enum):
    """Supported YouTube timestamp notations."""

    STANDARD = "standard"
    BRACKETED = "bracketed"


class PlaylistExportError(Exception):
    """Raised when companion playlist files cannot be created."""


@dataclass(frozen=True, slots=True)
class PlaylistExportResult:
    """Locations and text produced by one playlist companion-file export."""

    description_path: Path
    csv_path: Path
    description_text: str
    track_count: int


class PlaylistExportService:
    """Creates UTF-8 companion files using the same enabled-track order as rendering."""

    def export(self, tracks: Iterable[PlaylistTrack], output_directory: str | Path,
               timestamp_format: TimestampFormat = TimestampFormat.STANDARD,
               overwrite: bool = False) -> PlaylistExportResult:
        """Write ``description.txt`` and ``playlist.csv`` and return their details."""
        selected_tracks = [track for track in tracks if track.enabled]
        if not selected_tracks:
            raise PlaylistExportError("Select at least one playlist track before creating files.")

        directory = Path(output_directory).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PlaylistExportError(f"Could not create the output folder: {directory}") from error
        if not directory.is_dir():
            raise PlaylistExportError(f"The output path is not a folder: {directory}")

        description_text = self.description_text(selected_tracks, timestamp_format)
        description_path = directory / "description.txt"
        csv_path = directory / "playlist.csv"
        existing = [path.name for path in (description_path, csv_path) if path.exists()]
        if existing and not overwrite:
            raise PlaylistExportError(
                f"The output folder already contains: {', '.join(existing)}"
            )
        try:
            description_path.write_text(description_text, encoding="utf-8", newline="\n")
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("Track", "Title", "Artist", "Album", "Duration"))
                for number, track in enumerate(selected_tracks, start=1):
                    writer.writerow((
                        number,
                        track.title,
                        track.artist,
                        track.album,
                        self.format_timestamp(track.duration_seconds),
                    ))
        except OSError as error:
            raise PlaylistExportError("Could not write the playlist companion files.") from error

        return PlaylistExportResult(
            description_path=description_path,
            csv_path=csv_path,
            description_text=description_text,
            track_count=len(selected_tracks),
        )

    def description_text(self, tracks: Iterable[PlaylistTrack],
                         timestamp_format: TimestampFormat) -> str:
        """Build timestamp lines from enabled track order and explicit timeline gaps."""
        cursor = 0.0
        lines: list[str] = []
        for track in tracks:
            requested_start = track.start_time_seconds
            start = max(cursor, requested_start) if requested_start is not None else cursor
            timestamp = self.format_timestamp(start)
            prefix = f"[{timestamp}]" if timestamp_format is TimestampFormat.BRACKETED else timestamp
            lines.append(f"{prefix} {track.artist} - {track.title}")
            cursor = start + max(0.0, track.duration_seconds)
        return "\n".join(lines) + "\n"

    @staticmethod
    def format_timestamp(seconds: float) -> str:
        """Format seconds as MM:SS, switching to HH:MM:SS beyond one hour."""
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
        return f"{minutes:02d}:{seconds_part:02d}"
