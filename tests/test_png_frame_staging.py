from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage

from app.renderer.png_frame_staging import PngFrameStagingPipeline


class PngFrameStagingPipelineTests(unittest.TestCase):
    def test_capture_producer_can_continue_while_png_writer_is_busy(self) -> None:
        writer_started = threading.Event()
        release_writer = threading.Event()
        written: list[tuple[str, int]] = []

        class BlockingWriter:
            def __init__(self, path: str, _format: bytes) -> None:
                self.path = Path(path)

            def setCompression(self, _level: int) -> None:  # noqa: N802
                pass

            def setOptimizedWrite(self, _enabled: bool) -> None:  # noqa: N802
                pass

            def write(self, _image: QImage) -> bool:
                writer_started.set()
                if not release_writer.wait(2.0):
                    return False
                self.path.write_bytes(b"png-test")
                return True

            def errorString(self) -> str:  # noqa: N802
                return "blocked writer failed"

        with TemporaryDirectory(prefix="pvs-png-pipeline-") as raw_directory:
            path = Path(raw_directory) / "frame.png"
            image = QImage(32, 24, QImage.Format.Format_ARGB32)
            image.fill(QColor("#336699"))
            cancel = threading.Event()
            with patch(
                "app.renderer.png_frame_staging.QImageWriter", BlockingWriter,
            ):
                pipeline = PngFrameStagingPipeline(
                    lambda key, _image, size: written.append((key, size)),
                    cancel_event=cancel,
                    queue_capacity=2,
                    worker_count=1,
                )
                pipeline.submit(image, path, "base")
                self.assertTrue(writer_started.wait(1.0))
                # submit() has returned although compression/write is still
                # blocked in the consumer, proving capture and staging overlap.
                self.assertFalse(path.exists())
                release_writer.set()
                pipeline.finish()

            self.assertEqual(path.read_bytes(), b"png-test")
            self.assertEqual(written, [("base", len(b"png-test"))])
            self.assertLessEqual(pipeline.peak_buffered_frames, 2)

    def test_two_writers_compress_independent_frames_in_parallel(self) -> None:
        both_started = threading.Event()
        release_writers = threading.Event()
        active_lock = threading.Lock()
        active_writers = 0

        class ParallelWriter:
            def __init__(self, path: str, _format: bytes) -> None:
                self.path = Path(path)

            def setCompression(self, _level: int) -> None:  # noqa: N802
                pass

            def setOptimizedWrite(self, _enabled: bool) -> None:  # noqa: N802
                pass

            def write(self, _image: QImage) -> bool:
                nonlocal active_writers
                with active_lock:
                    active_writers += 1
                    if active_writers >= 2:
                        both_started.set()
                if not release_writers.wait(2.0):
                    return False
                self.path.write_bytes(b"png-parallel")
                return True

            def errorString(self) -> str:  # noqa: N802
                return "parallel writer failed"

        with TemporaryDirectory(prefix="pvs-png-parallel-") as raw_directory:
            root = Path(raw_directory)
            image = QImage(32, 24, QImage.Format.Format_ARGB32)
            image.fill(QColor("#336699"))
            cancel = threading.Event()
            with patch(
                "app.renderer.png_frame_staging.QImageWriter", ParallelWriter,
            ):
                pipeline = PngFrameStagingPipeline(
                    lambda _key, _image, _size: None,
                    cancel_event=cancel,
                    queue_capacity=2,
                    worker_count=2,
                )
                pipeline.submit(image, root / "frame-1.png", "base")
                pipeline.submit(image, root / "frame-2.png", "layer:0")
                self.assertTrue(both_started.wait(1.0))
                self.assertEqual(pipeline.worker_count, 2)
                release_writers.set()
                pipeline.finish()

            self.assertEqual((root / "frame-1.png").read_bytes(), b"png-parallel")
            self.assertEqual((root / "frame-2.png").read_bytes(), b"png-parallel")
            self.assertLessEqual(pipeline.peak_buffered_frames, 2)


if __name__ == "__main__":
    unittest.main()
