"""Orchestrates structure-analyzing a batch of playlist tracks through cache + provider.

A leaner, structure-specific counterpart to
``app.automix.analysis.service.AnalysisService`` (same batching/caching/
cancellation shape: group identical source files, analyze concurrently on
a bounded thread pool, isolate one track's failure from the rest) rather
than a shared abstraction between the two -- they operate on different
result types with different cache namespaces, and the two provider
ecosystems (rhythm vs. structure) are meant to evolve independently.

No Qt here on purpose, same reasoning as AnalysisService: a caller wanting
off-thread execution wraps this in QThread/QRunnable and forwards
``cancel_event``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace

from app.automix.cache import canonical_media_path
from app.automix.structure.cache import StructureAnalysisCache
from app.automix.structure.models import TrackStructureAnalysis
from app.automix.structure.provider import StructureAnalysisCancelled, StructureAnalysisProvider
from app.models.playlist import PlaylistTrack

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
"""(completed_tracks, total_tracks, message) -> None."""
ResultCallback = Callable[[str, "TrackStructureAnalysis | None"], None]
"""(track_id, structure or None on failure) -> None, as each track finishes."""


@dataclass(frozen=True, slots=True)
class StructureAnalysisBatchResult:
    """Per-track outcomes from one ``analyze_tracks`` call, keyed by track ID.

    A track ID absent from both ``analyses`` and ``failures`` was skipped
    because cancellation fired before its analysis started.
    """

    analyses: dict[str, TrackStructureAnalysis]
    failures: dict[str, str]


class StructureAnalysisService:
    """Resolve TrackStructureAnalysis for a batch of tracks via cache + one provider."""

    def __init__(
        self, provider: StructureAnalysisProvider, *,
        cache: StructureAnalysisCache | None = None,
        use_cache: bool = True,
        max_workers: int | None = None,
    ) -> None:
        self.provider = provider
        self.max_workers = max_workers
        self.cache = (
            cache or StructureAnalysisCache(analyzer_id=provider.provider_id, analyzer_version=provider.version)
        ) if use_cache else None

    def analyze_tracks(
        self, tracks: Sequence[PlaylistTrack], *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> StructureAnalysisBatchResult:
        """Analyze every track, deduplicating identical source files.

        Two tracks pointing at the exact same media are analyzed once and
        the result is cloned per track ID.
        """
        cancel_event = cancel_event or threading.Event()
        groups, order = self._group_by_source(tracks)
        analyses: dict[str, TrackStructureAnalysis] = {}
        failures: dict[str, str] = {}
        total = len(tracks)
        completed = 0

        def report(message: str) -> None:
            if progress is not None:
                progress(completed, total, message)

        if not order:
            return StructureAnalysisBatchResult(analyses, failures)

        report("AutoMix structure analysis started")
        LOGGER.info(
            "AutoMix structure analysis started: %d track(s), %d unique file(s)", total, len(order),
        )
        worker_count = max(1, min(self.max_workers or min(4, os.cpu_count() or 1), len(order)))

        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="automix-structure",
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
                    report(f"Structure analysis completed: {os.path.basename(group[0].file_path)}")
                elif error is not None:
                    for track in group:
                        failures[track.id] = error
                    report(f"Structure analysis failed: {os.path.basename(group[0].file_path)}")
                else:
                    report("Structure analysis cancelled")
                if on_result is not None and (result is not None or error is not None):
                    for track in group:
                        on_result(track.id, analyses.get(track.id))

        if cancel_event.is_set():
            LOGGER.info(
                "AutoMix structure analysis cancelled: %d/%d track(s) completed", len(analyses), total,
            )
        else:
            LOGGER.info(
                "AutoMix structure analysis completed: %d succeeded, %d failed, %d total",
                len(analyses), len(failures), total,
            )
        return StructureAnalysisBatchResult(analyses, failures)

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
    ) -> tuple[str, TrackStructureAnalysis | None, str | None]:
        representative = group[0]
        path_key = canonical_media_path(representative.file_path)
        if self.cache is not None:
            cached_fields = self.cache.load(representative.file_path)
            if cached_fields is not None:
                cached = TrackStructureAnalysis.from_cache_fields(
                    representative.id, representative.file_path, cached_fields,
                )
                return path_key, cached, None
        if cancel_event.is_set():
            return path_key, None, None
        try:
            result = self.provider.analyze(representative, cancel_event=cancel_event)
        except StructureAnalysisCancelled:
            return path_key, None, None
        except Exception as error:  # noqa: BLE001 - one bad track must not abort the batch
            LOGGER.warning("AutoMix structure analysis failed for %s: %s", representative.file_path, error)
            return path_key, None, str(error) or error.__class__.__name__
        # Only a genuine analyze() success ever reaches here -- never cache
        # a fallback/degraded result as if it were one (there is no such
        # fallback path for structure analysis; see sonara.py's module
        # docstring on why this cannot repeat the Beat This! fallback
        # cache-poisoning bug).
        if self.cache is not None:
            try:
                self.cache.store(representative.file_path, result)
            except OSError as error:
                LOGGER.warning(
                    "AutoMix structure cache write failed for %s: %s", representative.file_path, error,
                )
        return path_key, result, None
