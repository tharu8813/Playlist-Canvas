"""Playlist business logic and mutagen metadata integration."""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from uuid import uuid4

from mutagen import File as MutagenFile
from PySide6.QtCore import QObject, QSettings, Signal

from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import FFmpegNotFoundError, FFmpegRenderer
from app.utils.subprocess_utils import hidden_process_kwargs

LOGGER = logging.getLogger(__name__)
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}


@dataclass(slots=True)
class AudioImportCandidate:
    """A playable track plus the tag fields that require user attention."""

    track: PlaylistTrack
    missing_fields: tuple[str, ...] = ()


class PlaylistService(QObject):
    """Owns playlist ordering, track state, and audio-tag extraction."""

    playlist_changed = Signal()
    metadata_failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tracks: list[PlaylistTrack] = []

    @property
    def tracks(self) -> list[PlaylistTrack]:
        """Return a defensive copy of playlist order."""
        return list(self._tracks)

    def add_files(self, paths: Iterable[str | Path]) -> int:
        """Read and append all valid audio files, returning the number added."""
        return self.add_tracks(
            candidate.track for candidate in self.inspect_files(paths)
        )

    def inspect_files(
        self, paths: Iterable[str | Path],
    ) -> list[AudioImportCandidate]:
        """Read valid audio files and report which common tags are absent."""
        candidates: list[AudioImportCandidate] = []
        for raw_path in paths:
            path = Path(raw_path)
            if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
                continue
            candidates.append(self._read_candidate(path))
        return candidates

    def add_tracks(self, tracks: Iterable[PlaylistTrack]) -> int:
        """Append already-inspected tracks as one observable transaction."""
        prepared = list(tracks)
        if prepared:
            self._tracks.extend(prepared)
            self.playlist_changed.emit()
        return len(prepared)

    def _read_track(self, path: Path) -> PlaylistTrack | None:
        """Extract common tags while retaining usable tracks with missing metadata."""
        return self._read_candidate(path).track

    def _read_candidate(self, path: Path) -> AudioImportCandidate:
        """Extract a track and preserve knowledge of genuinely missing tags."""
        try:
            audio = MutagenFile(path, easy=True)
            tags = getattr(audio, "tags", None) or {}
            info = getattr(audio, "info", None)
            raw_values = {
                "title": self._tag_value(tags, "title").strip(),
                "artist": self._tag_value(tags, "artist").strip(),
                "album": self._tag_value(tags, "album").strip(),
            }
            missing = tuple(name for name, value in raw_values.items() if not value)
            title = raw_values["title"] or path.stem
            artist = raw_values["artist"] or "Unknown Artist"
            album = raw_values["album"] or "Unknown Album"
            duration = float(getattr(info, "length", 0.0))
            duration = duration if isfinite(duration) and duration >= 0 else 0.0
            if duration <= 0.0:
                duration = self._probe_duration(path)
            return AudioImportCandidate(
                PlaylistTrack(str(path.resolve()), title, artist, album, duration),
                missing,
            )
        except Exception as error:  # mutagen exposes format-specific exceptions
            LOGGER.warning("Unable to read audio metadata: %s", path, exc_info=error)
            self.metadata_failed.emit(str(path), str(error))
            return AudioImportCandidate(
                PlaylistTrack(
                    str(path.resolve()), path.stem,
                    duration_seconds=self._probe_duration(path),
                ),
                ("title", "artist", "album"),
            )

    @staticmethod
    def _probe_duration(path: Path) -> float:
        """Use FFprobe when tag readers cannot determine a playable file's length."""
        configured = str(QSettings().value("export/ffmpeg_path", "") or "").strip()
        try:
            ffmpeg = FFmpegRenderer.find_executable(configured or None)
            sibling_name = "ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"
            probe = ffmpeg.with_name(sibling_name)
        except FFmpegNotFoundError:
            found = shutil.which("ffprobe")
            probe = Path(found) if found else Path()
        if not probe.is_file():
            found = shutil.which("ffprobe")
            probe = Path(found) if found else Path()
        if not probe.is_file():
            return 0.0
        command = [
            str(probe), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ]
        kwargs: dict[str, object] = {
            "capture_output": True, "text": True, "timeout": 15, "check": False,
        }
        kwargs.update(hidden_process_kwargs())
        try:
            completed = subprocess.run(command, **kwargs)
            duration = float(completed.stdout.strip()) if completed.returncode == 0 else 0.0
            return duration if isfinite(duration) and duration > 0.0 else 0.0
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            LOGGER.warning("FFprobe could not determine audio duration: %s", path, exc_info=True)
            return 0.0

    @staticmethod
    def _tag_value(tags: object, key: str) -> str:
        """Read a tag whether mutagen returns an EasyID3 map or a mapping-like object."""
        try:
            value = tags.get(key)  # type: ignore[union-attr]
        except (AttributeError, KeyError):
            return ""
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else ""
        return str(value) if value else ""

    def remove(self, track_ids: Iterable[str]) -> None:
        """Delete the specified tracks from the playlist."""
        identifiers = set(track_ids)
        before = len(self._tracks)
        self._tracks = [track for track in self._tracks if track.id not in identifiers]
        if len(self._tracks) != before:
            self.playlist_changed.emit()

    def duplicate(self, track_ids: Iterable[str]) -> None:
        """Duplicate selected tracks immediately after their originals."""
        identifiers = set(track_ids)
        result: list[PlaylistTrack] = []
        for track in self._tracks:
            result.append(track)
            if track.id in identifiers:
                result.append(PlaylistTrack(
                    file_path=track.file_path, title=track.title, artist=track.artist,
                    album=track.album, duration_seconds=track.duration_seconds,
                    enabled=track.enabled, lyrics_path=track.lyrics_path,
                    lyrics=[cue.copy() for cue in track.lyrics],
                    lyrics_timing_offset_seconds=track.lyrics_timing_offset_seconds,
                ))
        changed = len(result) != len(self._tracks)
        self._tracks = result
        if changed:
            self.playlist_changed.emit()

    def set_enabled(self, track_id: str, enabled: bool) -> None:
        """Toggle whether a track will be included in a future render."""
        for track in self._tracks:
            if track.id == track_id:
                if track.enabled == enabled:
                    return
                track.enabled = enabled
                self.playlist_changed.emit()
                return

    def set_enabled_many(self, track_ids: Iterable[str], enabled: bool) -> None:
        """Toggle several tracks as one observable playlist transaction."""
        identifiers = set(track_ids)
        changed = False
        for track in self._tracks:
            if track.id in identifiers and track.enabled != enabled:
                track.enabled = enabled
                changed = True
        if changed:
            self.playlist_changed.emit()

    def update_track(self, track_id: str, **changes: object) -> None:
        """Update editable track details and notify all playlist views."""
        for track in self._tracks:
            if track.id == track_id:
                for name, value in changes.items():
                    if hasattr(track, name):
                        if name == "lyrics_timing_offset_seconds":
                            try:
                                numeric = float(value)
                            except (TypeError, ValueError):
                                continue
                            if not isfinite(numeric):
                                continue
                            value = max(-3_600.0, min(3_600.0, numeric))
                        setattr(track, name, value)
                self.playlist_changed.emit()
                return

    def reorder(self, track_ids: Iterable[str]) -> None:
        """Apply a new visual order received from the draggable playlist view."""
        identifiers = list(track_ids)
        if len(identifiers) != len(set(identifiers)) or len(identifiers) != len(self._tracks):
            return
        by_id = {track.id: track for track in self._tracks}
        ordered = [by_id[track_id] for track_id in identifiers if track_id in by_id]
        if len(ordered) == len(self._tracks) and ordered != self._tracks:
            self._tracks = ordered
            self.playlist_changed.emit()

    def move_track(self, track_id: str, direction: int) -> None:
        """Move one track one position in the playlist and timeline sequence."""
        index = next((i for i, track in enumerate(self._tracks) if track.id == track_id), -1)
        target = index + direction
        if index < 0 or target < 0 or target >= len(self._tracks):
            return
        self._tracks[index], self._tracks[target] = self._tracks[target], self._tracks[index]
        self.playlist_changed.emit()

    def set_start_time(self, track_id: str, seconds: float | None) -> None:
        """Set a manual timeline start or return a track to automatic sequencing."""
        for track in self._tracks:
            if track.id == track_id:
                track.start_time_seconds = (
                    max(self.minimum_start_time(track_id), seconds)
                    if seconds is not None else None
                )
                if (track.start_time_seconds is not None
                        and not isfinite(track.start_time_seconds)):
                    track.start_time_seconds = self.minimum_start_time(track_id)
                self.playlist_changed.emit()
                return

    def minimum_start_time(self, track_id: str) -> float:
        """Return the previous track's effective end time for one timeline row."""
        cursor = 0.0
        for track in self._tracks:
            if track.id == track_id:
                return cursor
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            cursor = start + track.duration_seconds
        return 0.0

    def timeline_tracks(self) -> list[tuple[PlaylistTrack, float, float]]:
        """Return tracks with effective start and end positions in current order."""
        cursor = 0.0
        timeline: list[tuple[PlaylistTrack, float, float]] = []
        for track in self._tracks:
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            end = start + track.duration_seconds
            timeline.append((track, start, end))
            cursor = end
        return timeline

    def replace(self, tracks: Iterable[PlaylistTrack]) -> None:
        """Replace the entire playlist, used by the Phase 1D project loader."""
        self._tracks = list(tracks)
        seen: set[str] = set()
        for track in self._tracks:
            if not track.id or track.id in seen:
                track.id = str(uuid4())
            seen.add(track.id)
            duration = float(track.duration_seconds)
            track.duration_seconds = duration if isfinite(duration) and duration >= 0 else 0.0
            if track.start_time_seconds is not None:
                start = float(track.start_time_seconds)
                track.start_time_seconds = max(0.0, start) if isfinite(start) else None
            offset = float(track.lyrics_timing_offset_seconds)
            track.lyrics_timing_offset_seconds = (
                max(-3_600.0, min(3_600.0, offset)) if isfinite(offset) else 0.0
            )
        self.playlist_changed.emit()
