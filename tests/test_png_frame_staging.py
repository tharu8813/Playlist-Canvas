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


if __name__ == "__main__":
    unittest.main()
