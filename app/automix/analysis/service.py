"""Orchestrates analyzing a batch of playlist tracks through cache + provider.

No Qt here on purpose (roadmap 1.7): a caller wanting off-thread execution
wraps this in QThread/QRunnable and forwards ``cancel_event``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace

from app.automix.analysis.provider import AnalysisCancelled, AnalysisProvider
from app.automix.cache import AnalysisCache, canonical_media_path
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixAnalysisSettings
from app.models.playlist import PlaylistTrack

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
"""(completed_tracks, total_tracks, message) -> None."""


@dataclass(frozen=True, slots=True)
class AnalysisBatchResult:
    """Per-track outcomes from one ``analyze_tracks`` call, keyed by track ID.

    A track ID absent from both ``analyses`` and ``failures`` was skipped
    because cancellation fired before its analysis started.
    """

    analyses: dict[str, TrackAnalysis]
    failures: dict[str, str]


class AnalysisService:
    """Resolve TrackAnalysis for a batch of tracks via cache + one provider."""

    def __init__(
        self, provider: AnalysisProvider, *,
        cache: AnalysisCache | None = None,
        settings: AutoMixAnalysisSettings | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or AutoMixAnalysisSettings()
        self.cache = (
            cache or AnalysisCache(analyzer_id=provider.provider_id, analyzer_version=provider.version)
        ) if self.settings.use_cache else None

    def analyze_tracks(
        self, tracks: Sequence[PlaylistTrack], *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> AnalysisBatchResult:
        """Analyze every track, deduplicating identical source files.

        Two tracks pointing at the exact same media are analyzed once and
        the result is cloned per track ID -- see module docstring and
        roadmap Phase 1 section 6 ("cache reuse is allowed").
        """
        cancel_event = cancel_event or threading.Event()
        groups, order = self._group_by_source(tracks)
        analyses: dict[str, TrackAnalysis] = {}
        failures: dict[str, str] = {}
        total = len(tracks)
        completed = 0

        def report(message: str) -> None:
            if progress is not None:
                progress(completed, total, message)

        if not order:
            return AnalysisBatchResult(analyses, failures)

        report("AutoMix analysis started")
        LOGGER.info(
            "AutoMix analysis started: %d track(s), %d unique file(s)", total, len(order),
        )
        worker_count = max(1, min(self.settings.max_workers or min(4, os.cpu_count() or 1), len(order)))

        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="automix-analysis",
        ) as executor:
            futures = {}
            for path_key in order:
                if cancel_event.is_set():
                    break
                futures[executor.submit(self._analyze_one, groups[path_key], cancel_event)] = path_key
            for future in as_completed(futures):
                path_key, result, error = future.result()
                group = groups[path_key]
                completed += len(group)
                if result is not None:
                    for track in group:
                        analyses[track.id] = replace(
                            result, track_id=track.id, source_path=track.file_path,
                        )
                    report(f"AutoMix analysis completed: {os.path.basename(group[0].file_path)}")
                elif error is not None:
                    for track in group:
                        failures[track.id] = error
                    report(f"AutoMix analysis failed: {os.path.basename(group[0].file_path)}")
                else:
                    report("AutoMix analysis cancelled")

        if cancel_event.is_set():
            LOGGER.info(
                "AutoMix analysis cancelled: %d/%d track(s) completed", len(analyses), total,
            )
        else:
            LOGGER.info(
                "AutoMix analysis completed: %d succeeded, %d failed, %d total",
                len(analyses), len(failures), total,
            )
        return AnalysisBatchResult(analyses, failures)

    @staticmethod
    def _group_by_source(
        tracks: Sequence[PlaylistTrack],
    ) -> tuple[dict[str, list[PlaylistTrack]], list[str]]:
        groups: dict[str, list[PlaylistTrack]] = {}
        order: list[str] = []
        for track in tracks:
            try:
                key = canonical_media_path(track.file_path)
            except OSError:
                key = track.file_path
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(track)
        return groups, order

    def _analyze_one(
        self, group: list[PlaylistTrack], cancel_event: threading.Event,
    ) -> tuple[str, TrackAnalysis | None, str | None]:
        representative = group[0]
        path_key = canonical_media_path(representative.file_path)
        if self.cache is not None:
            cached_fields = self.cache.load(representative.file_path)
            if cached_fields is not None:
                cached = TrackAnalysis.from_cache_fields(
                    representative.id, representative.file_path, cached_fields,
                )
                # A hybrid provider (e.g. BeatThisAnalysisProvider) that
                # degraded to a fallback result stamps that result with the
                # fallback's own analyzer_id (e.g. "basic"), not this
                # provider's -- even though AnalysisCache is namespaced by
                # this provider's identity (provider.provider_id/version).
                # Accepting such an entry as a hit would let one transient
                # failure permanently shadow this provider (a later run
                # with the real dependency/model available would keep
                # replaying the stale fallback instead of ever trying
                # again). Treat a provenance mismatch as a miss instead.
                if cached.analyzer_id == self.provider.provider_id:
                    return path_key, cached, None
        if cancel_event.is_set():
            return path_key, None, None
        try:
            result = self.provider.analyze(representative, cancel_event=cancel_event)
        except AnalysisCancelled:
            return path_key, None, None
        except Exception as error:  # noqa: BLE001 - one bad track must not abort the batch
            LOGGER.warning("AutoMix analysis failed for %s: %s", representative.file_path, error)
            return path_key, None, str(error) or error.__class__.__name__
        if self.cache is not None:
            try:
                self.cache.store(representative.file_path, result)
            except OSError as error:
                LOGGER.warning(
                    "AutoMix cache write failed for %s: %s", representative.file_path, error,
                )
        return path_key, result, None
