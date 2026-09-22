"""Versioned, on-disk cache for AutoMix track analysis.

Keyed by file fingerprint (path + size + mtime), not by track ID or
analyzer identity alone, so a media replacement is detected automatically
and switching analyzers never returns a stale result under a new meaning
(the analyzer identity is folded into the cache key itself).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.automix.models import TrackAnalysis

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1


_CACHE_FILE_PATTERNS = ("*.json", "*.tmp")
"""Entries, plus temp files an interrupted atomic write can leave behind."""


def cache_directories() -> tuple[Path, ...]:
    """Every on-disk AutoMix analysis cache (rhythm, structure)."""
    from app.automix.structure.cache import StructureAnalysisCache

    return AnalysisCache.default_root(), StructureAnalysisCache.default_root()


def _cache_files(directories: tuple[Path, ...]) -> list[Path]:
    return [path for directory in directories if directory.is_dir()
            for pattern in _CACHE_FILE_PATTERNS for path in directory.glob(pattern)]


def cache_usage(directories: tuple[Path, ...] | None = None) -> tuple[int, int]:
    """(entry count, total bytes) of the AutoMix analysis caches.

    Entries are never pruned on their own: a changed file or analyzer version
    just stops matching, so old entries accumulate until cleared.
    """
    entries = total = 0
    for path in _cache_files(directories or cache_directories()):
        try:
            total += path.stat().st_size
        except OSError:
            continue
        if path.suffix == ".json":
            entries += 1
    return entries, total


def clear_caches(directories: tuple[Path, ...] | None = None) -> int:
    """Delete every cached analysis; returns how many files went.

    Safe at any time: results are recomputed on demand, and a write racing
    this simply lands as a fresh entry (writes are atomic replaces).
    """
    removed = 0
    for path in _cache_files(directories or cache_directories()):
        try:
            path.unlink()
            removed += 1
        except OSError as error:  # in use by a concurrent reader on Windows: leave it
            LOGGER.info("AutoMix cache file not removed (%s): %s", error, path)
    return removed


def canonical_media_path(path: str) -> str:
    """Normalize a media path so the same file always hashes identically."""
    return os.path.normcase(str(Path(path).expanduser().resolve()))


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    canonical_path: str
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: str) -> "_FileFingerprint":
        canonical = canonical_media_path(path)
        stat = Path(canonical).stat()
        return cls(canonical, stat.st_size, stat.st_mtime_ns)


class AnalysisCache:
    """Reads and writes one JSON envelope per (file, analyzer) combination."""

    def __init__(
        self, root: Path | None = None, *,
        analyzer_id: str = "", analyzer_version: str = "",
    ) -> None:
        self.root = root or self.default_root()
        self.analyzer_id = analyzer_id
        self.analyzer_version = analyzer_version

    @staticmethod
    def default_root() -> Path:
        """Return the per-user folder AutoMix analysis results are cached in."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "PlaylistCanvas" / "automix-cache"

    def _entry_path(self, fingerprint: _FileFingerprint) -> Path:
        identity = "\0".join((
            fingerprint.canonical_path, str(fingerprint.size), str(fingerprint.mtime_ns),
            self.analyzer_id, self.analyzer_version, str(SCHEMA_VERSION),
        ))
        digest = sha256(identity.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def load(self, source_path: str) -> dict[str, Any] | None:
        """Return cached analysis fields for ``source_path``, or None on any miss.

        A missing file, a stale entry, a malformed one, or an entry from a
        different analyzer/schema are all treated the same way: a cache
        miss to be recomputed, never a crash.
        """
        try:
            fingerprint = _FileFingerprint.of(source_path)
        except OSError:
            return None
        entry_path = self._entry_path(fingerprint)
        try:
            envelope = json.loads(entry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            LOGGER.debug("AutoMix cache miss: %s", fingerprint.canonical_path)
            return None
        except (OSError, json.JSONDecodeError):
            LOGGER.info("AutoMix cache invalidated: malformed entry for %s", fingerprint.canonical_path)
            return None
        if not self._envelope_matches(envelope, fingerprint):
            LOGGER.info("AutoMix cache invalidated: stale entry for %s", fingerprint.canonical_path)
            return None
        try:
            fields = envelope["analysis"]
            if not isinstance(fields, dict):
                raise TypeError("analysis field must be an object")
            # Round-trip through TrackAnalysis to reject a cache entry whose
            # fields no longer pass validation (e.g. hand-edited or from a
            # future schema variant this version does not fully understand).
            TrackAnalysis.from_cache_fields("cache-check", fingerprint.canonical_path, fields)
        except (KeyError, TypeError, ValueError) as error:
            LOGGER.info(
                "AutoMix cache invalidated: invalid fields for %s (%s)",
                fingerprint.canonical_path, error,
            )
            return None
        LOGGER.debug("AutoMix cache hit: %s", fingerprint.canonical_path)
        return fields

    def store(self, source_path: str, analysis: TrackAnalysis) -> None:
        """Atomically write ``analysis`` for ``source_path``, replacing any prior entry."""
        fingerprint = _FileFingerprint.of(source_path)
        entry_path = self._entry_path(fingerprint)
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "analyzer_id": self.analyzer_id,
            "analyzer_version": self.analyzer_version,
            "file": {
                "path": fingerprint.canonical_path,
                "size": fingerprint.size,
                "mtime_ns": fingerprint.mtime_ns,
            },
            "analysis": analysis.to_cache_fields(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp", dir=self.root, delete=False,
            ) as temporary:
                json.dump(envelope, temporary, ensure_ascii=False)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.replace(entry_path)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        else:
            LOGGER.debug("AutoMix analysis cached: %s", fingerprint.canonical_path)

    def _envelope_matches(self, envelope: object, fingerprint: _FileFingerprint) -> bool:
        if not isinstance(envelope, dict) or envelope.get("schema_version") != SCHEMA_VERSION:
            return False
        if (envelope.get("analyzer_id") != self.analyzer_id
                or envelope.get("analyzer_version") != self.analyzer_version):
            return False
        file_info = envelope.get("file")
        if not isinstance(file_info, dict):
            return False
        return (
            file_info.get("path") == fingerprint.canonical_path
            and file_info.get("size") == fingerprint.size
            and file_info.get("mtime_ns") == fingerprint.mtime_ns
        )
