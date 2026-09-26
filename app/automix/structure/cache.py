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
stale result under a new meaning. Only the envelope read/write logic
is shared, via ``VersionedAnalysisCache``.
"""

from __future__ import annotations

import logging

from app.automix.cache import VersionedAnalysisCache
from app.automix.structure.models import TrackStructureAnalysis

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 2  # 2: content-keyed (was path + mtime)


class StructureAnalysisCache(VersionedAnalysisCache):
    """Reads and writes one JSON envelope per (file, structure analyzer) combination.

    Callers must only ever ``store`` after a genuine analyze() success --
    there is no fallback/degraded result for structure analysis to guard
    against here (see app/automix/structure/sonara.py's module docstring),
    unlike the hybrid Beat This! provider.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    ANALYSIS_TYPE = TrackStructureAnalysis
    DIRECTORY_NAME = "automix-structure-cache"
    LOG_LABEL = "AutoMix structure"
    LOGGER = LOGGER
