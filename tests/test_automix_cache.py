from __future__ import annotations

import json
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.automix.cache import (
    SCHEMA_VERSION, AnalysisCache, BoundedMemo, cache_usage, canonical_media_path, clear_caches, prune_caches,
)
from app.automix.models import TrackAnalysis


def _analysis(source_path: str) -> TrackAnalysis:
    return TrackAnalysis(
        track_id="ignored-on-store", source_path=source_path, duration_seconds=180.0,
        bpm=128.0, bpm_confidence=0.8, beats=(0.5, 1.0), analyzer_id="basic", analyzer_version="1",
    )


class AnalysisCacheTests(unittest.TestCase):
    def test_non_utf8_entries_in_both_caches_recover_as_misses(self) -> None:
        from app.automix.structure.cache import StructureAnalysisCache
        from app.automix.structure.models import TrackStructureAnalysis

        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            for cache_type, model in ((AnalysisCache, TrackAnalysis),
                                      (StructureAnalysisCache, TrackStructureAnalysis)):
                with self.subTest(cache=cache_type.__name__):
                    root = Path(directory) / cache_type.__name__
                    cache = cache_type(root, analyzer_id="test", analyzer_version="1")
                    cache.store(str(source), model(track_id="a", source_path=str(source), duration_seconds=1.0))
                    next(root.glob("*.json")).write_bytes(b"\xff\xfe\x00")
                    self.assertIsNone(cache.load(str(source)))

    def test_store_then_load_round_trips_fields(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="basic", analyzer_version="1")
            analysis = _analysis(str(source))
            cache.store(str(source), analysis)
            loaded = cache.load(str(source))
            self.assertIsNotNone(loaded)
            restored = TrackAnalysis.from_cache_fields("track-x", str(source), loaded)
            self.assertEqual(restored.bpm, 128.0)
            self.assertEqual(restored.beats, (0.5, 1.0))

    def test_missing_entry_is_a_cache_miss(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="basic", analyzer_version="1")
            self.assertIsNone(cache.load(str(source)))

    def test_size_change_invalidates_cache(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="basic", analyzer_version="1")
            cache.store(str(source), _analysis(str(source)))
            source.write_bytes(b"a longer audio payload now")
            self.assertIsNone(cache.load(str(source)))

    def test_same_content_at_a_new_path_and_mtime_is_still_a_hit(self) -> None:
        # .pvsproj media is re-extracted to a fresh temp path (new mtime) on
        # every open; that must not re-run a minute-long analysis per track.
        from app.automix.structure.cache import StructureAnalysisCache
        from app.automix.structure.models import TrackStructureAnalysis

        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "first-open" / "a.mp3"
            source.parent.mkdir()
            source.write_bytes(b"audio")
            reopened = Path(directory) / "second-open" / "renamed a.mp3"
            reopened.parent.mkdir()
            reopened.write_bytes(b"audio")
            future = time.time() + 5
            os.utime(reopened, (future, future))
            for cache_type, model in ((AnalysisCache, TrackAnalysis),
                                      (StructureAnalysisCache, TrackStructureAnalysis)):
                with self.subTest(cache=cache_type.__name__):
                    cache = cache_type(Path(directory) / cache_type.__name__,
                                       analyzer_id="basic", analyzer_version="1")
                    cache.store(str(source), model(track_id="a", source_path=str(source), duration_seconds=1.0))
                    self.assertIsNotNone(cache.load(str(reopened)))

    def test_same_size_content_change_invalidates_cache(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="basic", analyzer_version="1")
            cache.store(str(source), _analysis(str(source)))
            source.write_bytes(b"AUDIO")  # same size, different bytes
            future = time.time() + 5
            os.utime(source, (future, future))
            self.assertIsNone(cache.load(str(source)))

    def test_analyzer_version_change_invalidates_cache(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            root = Path(directory) / "cache"
            cache_v1 = AnalysisCache(root, analyzer_id="basic", analyzer_version="1")
            cache_v1.store(str(source), _analysis(str(source)))
            cache_v2 = AnalysisCache(root, analyzer_id="basic", analyzer_version="2")
            self.assertIsNone(cache_v2.load(str(source)))
            self.assertIsNotNone(cache_v1.load(str(source)))

    def test_analyzer_id_change_invalidates_cache(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            root = Path(directory) / "cache"
            AnalysisCache(root, analyzer_id="basic", analyzer_version="1").store(
                str(source), _analysis(str(source)),
            )
            other = AnalysisCache(root, analyzer_id="advanced", analyzer_version="1")
            self.assertIsNone(other.load(str(source)))

    def test_malformed_json_recovers_as_a_miss(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            root = Path(directory) / "cache"
            cache = AnalysisCache(root, analyzer_id="basic", analyzer_version="1")
            cache.store(str(source), _analysis(str(source)))
            entry_path = next(root.glob("*.json"))
            entry_path.write_text("{not valid json", encoding="utf-8")
            self.assertIsNone(cache.load(str(source)))

    def test_schema_version_mismatch_is_a_miss(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            root = Path(directory) / "cache"
            cache = AnalysisCache(root, analyzer_id="basic", analyzer_version="1")
            cache.store(str(source), _analysis(str(source)))
            entry_path = next(root.glob("*.json"))
            envelope = json.loads(entry_path.read_text(encoding="utf-8"))
            envelope["schema_version"] = SCHEMA_VERSION + 1
            entry_path.write_text(json.dumps(envelope), encoding="utf-8")
            self.assertIsNone(cache.load(str(source)))

    def test_invalid_analysis_fields_recover_as_a_miss(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            root = Path(directory) / "cache"
            cache = AnalysisCache(root, analyzer_id="basic", analyzer_version="1")
            cache.store(str(source), _analysis(str(source)))
            entry_path = next(root.glob("*.json"))
            envelope = json.loads(entry_path.read_text(encoding="utf-8"))
            envelope["analysis"]["bpm"] = -5.0
            entry_path.write_text(json.dumps(envelope), encoding="utf-8")
            self.assertIsNone(cache.load(str(source)))

    def test_store_is_atomic_no_temp_files_left_behind(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "a.mp3"
            source.write_bytes(b"audio")
            root = Path(directory) / "cache"
            cache = AnalysisCache(root, analyzer_id="basic", analyzer_version="1")
            cache.store(str(source), _analysis(str(source)))
            leftovers = list(root.glob("*.tmp"))
            self.assertEqual(leftovers, [])

    @unittest.skipUnless(os.name == "nt", "case-insensitive path normalization is Windows-specific")
    def test_canonical_media_path_is_case_insensitive_on_windows(self) -> None:
        with TemporaryDirectory(prefix="automix-cache-") as directory:
            source = Path(directory) / "Song.mp3"
            source.write_bytes(b"audio")
            self.assertEqual(
                canonical_media_path(str(source)), canonical_media_path(str(source).upper()),
            )


class CacheMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory(prefix="automix-cache-maintenance-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.media = self.root / "song.wav"
        self.media.write_bytes(b"audio")
        self.rhythm, self.structure = self.root / "rhythm", self.root / "structure"

    def _analysis(self) -> TrackAnalysis:
        return TrackAnalysis(track_id="t", source_path=str(self.media), duration_seconds=10.0, bpm=120.0)

    def test_usage_and_clear_cover_entries_and_orphaned_temp_files_only(self) -> None:
        AnalysisCache(self.rhythm, analyzer_id="a", analyzer_version="1").store(str(self.media), self._analysis())
        AnalysisCache(self.rhythm, analyzer_id="a", analyzer_version="2").store(str(self.media), self._analysis())
        self.structure.mkdir()
        (self.structure / "interrupted.tmp").write_text("{")
        unrelated = self.rhythm / "notes.txt"
        unrelated.write_text("keep me")
        entries, size = cache_usage((self.rhythm, self.structure))
        self.assertEqual(entries, 2)  # the stale version-1 entry counts until cleared
        self.assertGreater(size, 0)
        self.assertEqual(clear_caches((self.rhythm, self.structure)), 3)
        self.assertEqual(cache_usage((self.rhythm, self.structure)), (0, 0))
        self.assertTrue(unrelated.is_file())
        self.assertIsNone(AnalysisCache(self.rhythm, analyzer_id="a", analyzer_version="2").load(str(self.media)))

    def test_missing_cache_folders_are_empty_not_errors(self) -> None:
        self.assertEqual(cache_usage((self.root / "never-created",)), (0, 0))
        self.assertEqual(clear_caches((self.root / "never-created",)), 0)

    def test_concurrent_writers_and_readers_never_see_a_partial_entry(self) -> None:
        cache = AnalysisCache(self.rhythm, analyzer_id="a", analyzer_version="1")
        failures: list[Exception] = []

        def write() -> None:
            for _ in range(20):
                try:
                    cache.store(str(self.media), self._analysis())
                except OSError:
                    pass  # a Windows replace racing a reader; the service logs and moves on
                loaded = cache.load(str(self.media))
                if loaded is not None and loaded.get("bpm") != 120.0:
                    failures.append(AssertionError(loaded))

        threads = [threading.Thread(target=write) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(cache.load(str(self.media))["bpm"], 120.0)
        self.assertEqual(list(self.rhythm.glob("*.tmp")), [])

    def test_prune_removes_only_unused_entries_and_stale_temp_files(self) -> None:
        old_cache = AnalysisCache(self.rhythm, analyzer_id="a", analyzer_version="1")
        used_cache = AnalysisCache(self.rhythm, analyzer_id="a", analyzer_version="2")
        old_cache.store(str(self.media), self._analysis())
        used_cache.store(str(self.media), self._analysis())
        (self.rhythm / "fresh.tmp").write_text("{")
        (self.rhythm / "stale.tmp").write_text("{")
        (self.rhythm / "malformed.json").write_text("not json")
        long_ago = time.time() - 400 * 86_400
        for path in self.rhythm.iterdir():
            if path.name != "fresh.tmp":
                os.utime(path, (long_ago, long_ago))
        self.assertIsNotNone(used_cache.load(str(self.media)))  # a hit marks it used

        self.assertEqual(prune_caches((self.rhythm, self.root / "missing")), 3)
        self.assertIsNone(old_cache.load(str(self.media)))
        self.assertIsNotNone(used_cache.load(str(self.media)))
        self.assertTrue((self.rhythm / "fresh.tmp").is_file())


class BoundedMemoTests(unittest.TestCase):
    def test_capacity_is_never_exceeded_and_keeps_recent_entries(self) -> None:
        memo = BoundedMemo(128)
        for index in range(1000):
            memo.put(("song", index, 0), str(index))
            memo.get(("song", 0, 0))  # repeatedly used entries survive
        self.assertEqual(len(memo), 128)
        self.assertEqual(memo.get(("song", 0, 0)), "0")
        self.assertEqual(memo.get(("song", 999, 0)), "999")
        self.assertIsNone(memo.get(("song", 500, 0)))

    def test_concurrent_callers_of_one_key_compute_it_once(self) -> None:
        memo = BoundedMemo(8)
        calls: list[int] = []
        start = threading.Barrier(8)

        def compute() -> str:
            calls.append(1)
            time.sleep(0.05)
            return "digest"

        results: list[str] = []

        def caller() -> None:
            start.wait()
            results.append(memo.get_or_compute("key", compute))

        threads = [threading.Thread(target=caller) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["digest"] * 8)
        self.assertEqual(len(calls), 1)
        self.assertEqual(memo.get_or_compute("key", compute), "digest")
        self.assertEqual(len(calls), 1)

    def test_a_changed_file_is_hashed_again(self) -> None:
        from app.automix.cache import _FileFingerprint

        with TemporaryDirectory(prefix="automix-digest-") as directory:
            media = Path(directory) / "song.wav"
            media.write_bytes(b"first")
            first = _FileFingerprint.of(str(media)).content_sha256
            media.write_bytes(b"second, longer")
            self.assertNotEqual(_FileFingerprint.of(str(media)).content_sha256, first)

    def test_cancelled_or_failed_loudness_is_not_remembered(self) -> None:
        from unittest.mock import patch

        from app.controllers import progressive_automix_controller as controller

        with TemporaryDirectory(prefix="automix-levels-") as directory:
            media = Path(directory) / "song.wav"
            media.write_bytes(b"audio")
            cancel = threading.Event()
            with (
                patch.object(controller, "_LEVELS", BoundedMemo(8)),
                patch.object(controller, "measure_loudness", side_effect=[
                    (-float("inf"), -float("inf")), (-14.0, -1.0), (-9.0, -0.5),
                ]) as measure,
            ):
                controller.track_level(Path("ffmpeg"), str(media), cancel)  # failed
                cancel.set()
                controller.track_level(Path("ffmpeg"), str(media), cancel)  # cancelled
                cancel.clear()
                self.assertEqual(controller.track_level(Path("ffmpeg"), str(media), cancel), (-9.0, -0.5))
                self.assertEqual(controller.track_level(Path("ffmpeg"), str(media), cancel), (-9.0, -0.5))
                self.assertEqual(measure.call_count, 3)


if __name__ == "__main__":
    unittest.main()
