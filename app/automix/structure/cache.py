"""Versioned, on-disk cache for AutoMix structure analysis.

Deliberately a separate cache (own root directory, own namespace) from
``app.automix.cache.AnalysisCache``: structure analysis is an
independently-optional analyzer (Sonara) producing a different result
type (``TrackStructureAnalysis``) on its own schedule, and mixing the two
caches would make it harder to reason about invalidation when only one
side changes (e.g. upgrading Sonara must never touch rhythm-analysis cache
entries, and vice versa). Keyed by file content (SHA-256 + size), same
discipline as the rhythm cache, so a media replacement is detected
automatically and switching structure providers/versions never returns a
stale result under a new meaning.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.automix.cache import _FileFingerprint
from app.automix.structure.models import TrackStructureAnalysis

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 2  # 2: content-keyed (was path + mtime)


class StructureAnalysisCache:
    """Reads and writes one JSON envelope per (file, structure analyzer) combination."""

    def __init__(
        self, root: Path | None = None, *,
        analyzer_id: str = "", analyzer_version: str = "",
    ) -> None:
        self.root = root or self.default_root()
        self.analyzer_id = analyzer_id
        self.analyzer_version = analyzer_version

    @staticmethod
    def default_root() -> Path:
        """Return the per-user folder AutoMix structure analysis results are cached in."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "PlaylistCanvas" / "automix-structure-cache"

    def _entry_path(self, fingerprint: "_FileFingerprint") -> Path:
        return self.root / fingerprint.entry_name(self.analyzer_id, self.analyzer_version, str(SCHEMA_VERSION))

    def load(self, source_path: str) -> dict[str, Any] | None:
        """Return cached structure fields for ``source_path``, or None on any miss.

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
            LOGGER.debug("AutoMix structure cache miss: %s", fingerprint.canonical_path)
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.info("AutoMix structure cache invalidated: malformed entry for %s", fingerprint.canonical_path)
            return None
        if not self._envelope_matches(envelope, fingerprint):
            LOGGER.info("AutoMix structure cache invalidated: stale entry for %s", fingerprint.canonical_path)
            return None
        try:
            fields = envelope["analysis"]
            if not isinstance(fields, dict):
                raise TypeError("analysis field must be an object")
            # Round-trip through TrackStructureAnalysis to reject a cache
            # entry whose fields no longer pass validation (e.g. hand-edited
            # or from a future schema variant this version does not fully
            # understand) -- never hand malformed structure data to a caller.
            TrackStructureAnalysis.from_cache_fields("cache-check", fingerprint.canonical_path, fields)
        except (KeyError, TypeError, ValueError) as error:
            LOGGER.info(
                "AutoMix structure cache invalidated: invalid fields for %s (%s)",
                fingerprint.canonical_path, error,
            )
            return None
        LOGGER.debug("AutoMix structure cache hit: %s", fingerprint.canonical_path)
        return fields

    def store(self, source_path: str, analysis: TrackStructureAnalysis) -> None:
        """Atomically write ``analysis`` for ``source_path``, replacing any prior entry.

        Callers must only ever call this after a genuine analyze() success
        -- there is no fallback/degraded result for structure analysis to
        guard against here (see app/automix/structure/sonara.py's module
        docstring), unlike the hybrid Beat This! provider.
        """
        fingerprint = _FileFingerprint.of(source_path)
        entry_path = self._entry_path(fingerprint)
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "analyzer_id": self.analyzer_id,
            "analyzer_version": self.analyzer_version,
            "file": fingerprint.record(),
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
            LOGGER.debug("AutoMix structure analysis cached: %s", fingerprint.canonical_path)

    def _envelope_matches(self, envelope: object, fingerprint: "_FileFingerprint") -> bool:
        if not isinstance(envelope, dict) or envelope.get("schema_version") != SCHEMA_VERSION:
            return False
        if (envelope.get("analyzer_id") != self.analyzer_id
                or envelope.get("analyzer_version") != self.analyzer_version):
            return False
        return fingerprint.matches(envelope.get("file"))
