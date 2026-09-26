"""Read and write M3U8 (and legacy M3U) playlists of local audio files.

The playlist file itself is only a list of paths: importing it adds the songs it
points to, never the ``.m3u8`` file, to the project.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import locale
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from app.models.playlist import PlaylistTrack
from app.services.playlist_service import AUDIO_EXTENSIONS

PLAYLIST_FILE_EXTENSIONS = {".m3u8", ".m3u"}


class M3uPlaylistError(Exception):
    """Raised when a playlist file cannot be read or written."""


@dataclass(slots=True)
class M3uEntries:
    """Playable local audio files in playlist order, plus the entries left out."""

    audio_paths: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Missing files, web streams and non-audio entries, as written in the playlist."""


def read_m3u(path: str | Path) -> M3uEntries:
    """Return the local audio files a playlist lists, relative entries resolved to its folder."""
    playlist = Path(path)
    try:
        raw = playlist.read_bytes()
    except OSError as error:
        raise M3uPlaylistError(f"Could not read the playlist file: {playlist}") from error
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # M3U8 is UTF-8 by definition; a legacy .m3u uses the system code page.
        encoding = "utf-8" if playlist.suffix.lower() == ".m3u8" else locale.getpreferredencoding(False)
        text = raw.decode(encoding, errors="replace")
    entries = M3uEntries()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        location = _local_path(entry, playlist.parent)
        if location is None or location.suffix.lower() not in AUDIO_EXTENSIONS or not location.is_file():
            entries.skipped.append(entry)
            continue
        entries.audio_paths.append(location.resolve())
    return entries


def write_m3u8(tracks: Iterable[PlaylistTrack], path: str | Path) -> int:
    """Write an extended UTF-8 M3U8 playlist and return how many tracks it lists.

    Paths are written relative to the playlist's folder when possible, so the
    playlist keeps working when that folder moves together with its music.
    """
    playlist = Path(path)
    lines = ["#EXTM3U"]
    count = 0
    for track in tracks:
        seconds = round(track.duration_seconds) if track.duration_seconds > 0 else -1
        label = f"{track.artist} - {track.title}" if track.artist else track.title
        lines.append(f"#EXTINF:{seconds},{label}")
        lines.append(_playlist_entry(Path(track.file_path), playlist.parent))
        count += 1
    try:
        playlist.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError as error:
        raise M3uPlaylistError(f"Could not write the playlist file: {playlist}") from error
    return count


def _local_path(entry: str, base: Path) -> Path | None:
    if entry.lower().startswith("file:"):
        parsed = urlparse(entry)
        # file://server/share/x.mp3 keeps its UNC host; file:///C:/x.mp3 has none.
        location = f"//{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
        return Path(url2pathname(unquote(location)))
    if "://" in entry:
        return None  # http(s) streams and other remote entries
    location = Path(entry)
    return location if location.is_absolute() else base / location


def _playlist_entry(file_path: Path, directory: Path) -> str:
    try:
        return os.path.relpath(file_path, directory)
    except ValueError:  # another drive on Windows: only an absolute path works
        return str(file_path)
