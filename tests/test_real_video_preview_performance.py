"""Opt-in real-media preview smoke and soak tests.

Set ``PLAYLIST_CANVAS_TEST_FFMPEG`` to an FFmpeg executable for the short CPU
decode check. Also set ``PLAYLIST_CANVAS_RUN_PREVIEW_SOAK=1`` for the 60-second
GPU-layer soak; its duration, backend and RSS budget have dedicated environment
overrides below.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.dialogs.export_preview_dialog import ExportPreviewDialog
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.services.source_store import SourceStore
from app.utils.i18n import Translator
from app.utils.subprocess_utils import hidden_process_kwargs


MIB = 1024 * 1024


def _configured_ffmpeg() -> Path:
    configured = os.environ.get("PLAYLIST_CANVAS_TEST_FFMPEG", "").strip()
    if not configured:
        raise unittest.SkipTest(
            "Set PLAYLIST_CANVAS_TEST_FFMPEG to run real video preview checks."
        )
    executable = Path(configured)
    if not executable.is_file():
        raise AssertionError(f"Configured FFmpeg does not exist: {executable}")
    return executable


def _write_silence(path: Path, duration_seconds: float) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48_000)
        audio.writeframes(b"\0\0\0\0" * round(48_000 * duration_seconds))


def _generate_test_video(
    ffmpeg: Path, output: Path, duration_seconds: float,
) -> None:
    common = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-t", f"{duration_seconds:.3f}", "-pix_fmt", "yuv420p",
    ]
    attempts = (
        ["-c:v", "libx264", "-preset", "ultrafast"],
        ["-c:v", "mpeg4", "-q:v", "3"],
    )
    errors: list[str] = []
    for codec_args in attempts:
        result = subprocess.run(
            [*common, *codec_args, "-movflags", "+faststart", "-y", str(output)],
            capture_output=True,
            text=True,
            check=False,
            **hidden_process_kwargs(),
        )
        if result.returncode == 0 and output.is_file() and output.stat().st_size:
            return
        errors.append(result.stderr.strip())
    raise AssertionError("Could not generate the real preview fixture: " + " | ".join(errors))


def _resident_set_bytes() -> int | None:
    """Return current RSS without adding a psutil test dependency."""
    if sys.platform == "win32":
        size_t = ctypes.c_size_t

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", size_t),
                ("WorkingSetSize", size_t),
                ("QuotaPeakPagedPoolUsage", size_t),
                ("QuotaPagedPoolUsage", size_t),
                ("QuotaPeakNonPagedPoolUsage", size_t),
                ("QuotaNonPagedPoolUsage", size_t),
                ("PagefileUsage", size_t),
                ("PeakPagefileUsage", size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        )
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.WorkingSetSize) if ok else None
    if sys.platform.startswith("linux"):
        try:
            resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            return None
    return None


class RealVideoPreviewPerformanceTests(unittest.TestCase):
    """Decode generated MP4 files through the complete interactive preview path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _create_preview(
        self, directory: Path, *, timeline_seconds: float, backend: str,
    ) -> tuple[ExportPreviewDialog, SourceItem]:
        ffmpeg = _configured_ffmpeg()
        video_path = directory / "moving-pattern.mp4"
        audio_path = directory / "silence.wav"
        _generate_test_video(ffmpeg, video_path, min(6.0, timeline_seconds))
        _write_silence(audio_path, timeline_seconds)

        source = Source(
            SourceType.VIDEO,
            "Real decoder fixture",
            width=640,
            height=360,
            video_paths=[str(video_path)],
            video_repeat_mode="loop_one",
            video_cycle_unlimited=True,
            image_fit_mode="stretch",
        )
        item = SourceItem(source)
        scene = CanvasScene()
        scene.addItem(item)
        store = SourceStore()
        store.add(source)
        track = PlaylistTrack(
            str(audio_path), "Long preview fixture",
            duration_seconds=timeline_seconds,
        )
        preview = ExportPreviewDialog(
            scene, [track], Translator(), source_store=store,
            preferred_backend=backend,
        )
        # Duration probing remains separately covered. Seed only this metadata
        # so this test isolates actual decode, scheduling and composition cost.
        preview._video_duration_cache[str(video_path)] = min(6.0, timeline_seconds)
        preview.show()
        self.application.processEvents()
        return preview, item

    def _run_until_frames(
        self, preview: ExportPreviewDialog, item: SourceItem,
        minimum_frames: int, timeout_seconds: float,
    ) -> None:
        preview.play_button.setChecked(True)
        preview._toggle_playback(True)
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if item.video_decoder_stats().accepted_frames >= minimum_frames:
                return
            QTest.qWait(20)
        stats = item.video_decoder_stats()
        self.fail(
            "The real video decoder did not deliver enough preview frames "
            f"within {timeout_seconds:.1f}s: {stats}"
        )

    def test_real_mp4_advances_decoder_and_preview_timeline(self) -> None:
        _configured_ffmpeg()
        with TemporaryDirectory(prefix="playlist-real-preview-") as raw_directory:
            preview, item = self._create_preview(
                Path(raw_directory), timeline_seconds=12.0, backend="cpu",
            )
            try:
                self._run_until_frames(preview, item, 8, 10.0)
                stats = item.video_decoder_stats()
                self.assertGreaterEqual(stats.accepted_frames, 8)
                self.assertGreater(preview.timeline.value(), 0)
                self.assertTrue(item._video_timeline_preview_active)
                self.assertFalse(preview._last_preview_error_title)
            finally:
                preview._stop_preview()
                preview.close()
                self.application.processEvents()

    def test_long_real_mp4_preview_remains_bounded_and_responsive(self) -> None:
        if os.environ.get("PLAYLIST_CANVAS_RUN_PREVIEW_SOAK", "").strip() != "1":
            self.skipTest("Set PLAYLIST_CANVAS_RUN_PREVIEW_SOAK=1 for the long soak test.")
        _configured_ffmpeg()
        soak_seconds = min(
            600.0,
            max(15.0, float(os.environ.get("PLAYLIST_CANVAS_PREVIEW_SOAK_SECONDS", "60"))),
        )
        maximum_growth = max(
            32.0,
            float(os.environ.get("PLAYLIST_CANVAS_PREVIEW_SOAK_MAX_RSS_MB", "192")),
        ) * MIB
        backend = os.environ.get(
            "PLAYLIST_CANVAS_PREVIEW_SOAK_BACKEND", "gpu_layers",
        ).strip()
        if backend not in {"cpu", "gpu_layers"}:
            self.fail(f"Unsupported soak backend: {backend}")

        with TemporaryDirectory(prefix="playlist-preview-soak-") as raw_directory:
            preview, item = self._create_preview(
                Path(raw_directory),
                timeline_seconds=soak_seconds + 15.0,
                backend=backend,
            )
            heartbeat_count = 0

            def heartbeat() -> None:
                nonlocal heartbeat_count
                heartbeat_count += 1

            heartbeat_timer = QTimer()
            heartbeat_timer.setInterval(100)
            heartbeat_timer.timeout.connect(heartbeat)
            heartbeat_timer.start()
            try:
                self._run_until_frames(preview, item, 12, 12.0)
                # One generated clip is six seconds. Warming through a complete
                # loop keeps lazy decoder/GPU allocations out of the leak budget.
                warmup_deadline = monotonic() + 6.5
                while monotonic() < warmup_deadline:
                    QTest.qWait(25)
                start_stats = item.video_decoder_stats()
                start_timeline = preview.timeline.value()
                baseline_rss = _resident_set_bytes()
                peak_rss = baseline_rss
                heartbeat_start = heartbeat_count
                deadline = monotonic() + soak_seconds
                while monotonic() < deadline:
                    QTest.qWait(25)
                    current_rss = _resident_set_bytes()
                    if current_rss is not None:
                        peak_rss = max(peak_rss or current_rss, current_rss)

                end_stats = item.video_decoder_stats()
                accepted = end_stats.accepted_frames - start_stats.accepted_frames
                pressure_drops = end_stats.pressure_drops - start_stats.pressure_drops
                seeks = end_stats.seek_count - start_stats.seek_count
                heartbeats = heartbeat_count - heartbeat_start
                timeline_advance = (
                    preview.timeline.value() - start_timeline
                ) / 100.0

                self.assertGreaterEqual(accepted, int(soak_seconds * 3))
                self.assertGreaterEqual(heartbeats, int(soak_seconds * 5))
                self.assertGreaterEqual(timeline_advance, soak_seconds * 0.75)
                self.assertLessEqual(seeks, int(soak_seconds * 2) + 5)
                self.assertLessEqual(
                    pressure_drops, accepted * 3 + 60,
                    "Decoder pressure drops grew without a corresponding frame flow.",
                )
                self.assertFalse(preview._last_preview_error_title)
                if baseline_rss is not None and peak_rss is not None:
                    self.assertLessEqual(
                        peak_rss - baseline_rss,
                        maximum_growth,
                        "Long preview RSS grew beyond the configured budget: "
                        f"{(peak_rss - baseline_rss) / MIB:.1f} MiB > "
                        f"{maximum_growth / MIB:.1f} MiB",
                    )
            finally:
                heartbeat_timer.stop()
                preview._stop_preview()
                preview.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
