from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.video.preview_proxy import PreviewProxyCache, PreviewProxyWorker


class PreviewProxyTests(unittest.TestCase):
    def test_cache_identity_changes_when_original_media_changes(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-key-") as raw_directory:
            directory = Path(raw_directory)
            source = directory / "source.mp4"
            source.write_bytes(b"first")
            cache = PreviewProxyCache(directory / "cache")

            first = cache.proxy_path(source)
            source.write_bytes(b"second-version")
            second = cache.proxy_path(source)

        self.assertNotEqual(first, second)
        self.assertEqual(first.parent.name, "cache")
        self.assertEqual(first.suffix, ".mp4")

    def test_small_video_reuses_original_without_transcoding(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-small-") as raw_directory:
            directory = Path(raw_directory)
            ffmpeg = directory / "ffmpeg.exe"
            ffprobe = directory / "ffprobe.exe"
            source = directory / "small.mp4"
            ffmpeg.touch()
            ffprobe.touch()
            source.write_bytes(b"media")
            worker = PreviewProxyWorker(
                ffmpeg, source, PreviewProxyCache(directory / "cache"),
            )
            ready: list[tuple[str, str, bool]] = []
            worker.ready.connect(
                lambda original, preview, proxied:
                ready.append((original, preview, proxied))
            )
            probe = subprocess.CompletedProcess(
                [], 0,
                json.dumps({"streams": [{
                    "width": 640, "height": 360,
                    "avg_frame_rate": "30/1",
                }]}),
                "",
            )
            with patch.object(worker, "_execute", return_value=probe) as execute:
                worker.run()

            self.assertEqual(ready, [(str(source), str(source), False)])
            self.assertEqual(execute.call_count, 1)
            self.assertFalse((directory / "cache").exists())

    def test_large_video_creates_bounded_silent_proxy_and_reuses_it(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-large-") as raw_directory:
            directory = Path(raw_directory)
            ffmpeg = directory / "ffmpeg.exe"
            ffprobe = directory / "ffprobe.exe"
            source = directory / "large.mp4"
            ffmpeg.touch()
            ffprobe.touch()
            source.write_bytes(b"original-media-remains-unchanged")
            original_bytes = source.read_bytes()
            cache = PreviewProxyCache(directory / "cache")
            worker = PreviewProxyWorker(ffmpeg, source, cache)
            commands: list[list[str]] = []

            def execute(arguments: list[str]) -> subprocess.CompletedProcess[str]:
                commands.append(arguments)
                if Path(arguments[0]).name.lower().startswith("ffprobe"):
                    return subprocess.CompletedProcess(
                        arguments, 0,
                        json.dumps({"streams": [{
                            "width": 3840, "height": 2160,
                            "avg_frame_rate": "60/1",
                        }]}),
                        "",
                    )
                Path(arguments[-1]).write_bytes(b"generated-proxy")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            ready: list[tuple[str, str, bool]] = []
            worker.ready.connect(
                lambda original, preview, proxied:
                ready.append((original, preview, proxied))
            )
            with patch.object(worker, "_execute", side_effect=execute):
                worker.run()

            self.assertEqual(len(ready), 1)
            proxy = Path(ready[0][1])
            self.assertTrue(ready[0][2])
            self.assertTrue(proxy.is_file())
            self.assertEqual(source.read_bytes(), original_bytes)
            encode = commands[1]
            self.assertIn("-an", encode)
            self.assertIn("-threads", encode)
            self.assertEqual(encode[encode.index("-threads") + 1], "2")
            video_filter = encode[encode.index("-vf") + 1]
            self.assertIn("min(960,iw)", video_filter)
            self.assertIn("min(540,ih)", video_filter)
            self.assertIn("fps=fps=30", video_filter)

            cached_worker = PreviewProxyWorker(ffmpeg, source, cache)
            cached_ready: list[tuple[str, str, bool]] = []
            cached_worker.ready.connect(
                lambda original, preview, proxied:
                cached_ready.append((original, preview, proxied))
            )
            with patch.object(cached_worker, "_execute") as execute_cached:
                cached_worker.run()
            execute_cached.assert_not_called()
            self.assertEqual(cached_ready[0][1], str(proxy))

    def test_alpha_video_stays_on_original_to_preserve_transparency(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-alpha-") as raw_directory:
            directory = Path(raw_directory)
            ffmpeg = directory / "ffmpeg.exe"
            ffprobe = directory / "ffprobe.exe"
            source = directory / "transparent.webm"
            ffmpeg.touch()
            ffprobe.touch()
            source.write_bytes(b"alpha-media")
            worker = PreviewProxyWorker(
                ffmpeg, source, PreviewProxyCache(directory / "cache"),
            )
            ready: list[tuple[str, str, bool]] = []
            worker.ready.connect(
                lambda original, preview, proxied:
                ready.append((original, preview, proxied))
            )
            probe = subprocess.CompletedProcess(
                [], 0,
                json.dumps({"streams": [{
                    "width": 3840, "height": 2160,
                    "avg_frame_rate": "24/1", "pix_fmt": "yuva420p",
                }]}),
                "",
            )
            with patch.object(worker, "_execute", return_value=probe) as execute:
                worker.run()

            self.assertEqual(ready, [(str(source), str(source), False)])
            self.assertEqual(execute.call_count, 1)

    def test_cancelled_worker_does_not_leave_partial_proxy(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-cancel-") as raw_directory:
            directory = Path(raw_directory)
            ffmpeg = directory / "ffmpeg.exe"
            source = directory / "large.mp4"
            ffmpeg.touch()
            source.write_bytes(b"media")
            cache = PreviewProxyCache(directory / "cache")
            worker = PreviewProxyWorker(ffmpeg, source, cache)
            cancelled: list[str] = []
            worker.cancelled.connect(cancelled.append)

            worker.cancel()
            worker.run()

            self.assertEqual(cancelled, [str(source)])
            self.assertEqual(list((directory / "cache").glob("*.mp4")), [])

    def test_prune_removes_oldest_proxy_without_touching_protected_file(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-prune-") as raw_directory:
            root = Path(raw_directory) / "cache"
            root.mkdir()
            oldest = root / ("1" * 64 + ".mp4")
            newer = root / ("2" * 64 + ".mp4")
            protected = root / ("3" * 64 + ".mp4")
            for index, path in enumerate((oldest, newer, protected), start=1):
                with path.open("wb") as file:
                    file.truncate(30 * 1024 * 1024)
                path.touch()
                # Keep deterministic LRU ordering without sleeping.
                timestamp = 1_700_000_000 + index
                path.chmod(0o600)
                os.utime(path, (timestamp, timestamp))
            cache = PreviewProxyCache(root, maximum_bytes=64 * 1024 * 1024)

            cache.prune(protected={protected})

            self.assertFalse(oldest.exists())
            self.assertTrue(newer.exists())
            self.assertTrue(protected.exists())


if __name__ == "__main__":
    unittest.main()
