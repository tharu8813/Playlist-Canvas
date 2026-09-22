"""Opt-in integration test against the REAL sonara dependency.

Skipped by default: normal test runs (and CI) must never require an
optional dependency to be installed. Set PLAYLIST_CANVAS_TEST_SONARA=1 to
run this on a machine with `pip install sonara` already done.

Exact intro/outro seconds are intentionally not asserted against a
synthetic fixture (roadmap: "synthetic fixture에서 정확한 intro/outro 초를
강제 assertion하지 말 것") -- only structural sanity (non-empty results,
values within the track's own duration, hop > 0).
"""

from __future__ import annotations

import os
import struct
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.automix.structure.sonara import SonaraStructureProvider
from app.models.playlist import PlaylistTrack

_ENABLED = os.environ.get("PLAYLIST_CANVAS_TEST_SONARA") == "1"

_SAMPLE_RATE = 22050


def _write_energy_ramp_wav(path: Path, duration: float, sample_rate: int = _SAMPLE_RATE) -> None:
    """Quiet intro, loud middle, quiet outro -- gives a structure analyzer
    an actual energy-shape signal to segment, rather than flat noise."""
    t = np.arange(int(duration * sample_rate)) / sample_rate
    envelope = np.ones_like(t)
    fade_in = t < 8.0
    fade_out = t > duration - 8.0
    envelope[fade_in] = np.linspace(0.05, 1.0, int(np.sum(fade_in)))
    envelope[fade_out] = np.linspace(1.0, 0.05, int(np.sum(fade_out)))
    signal = (0.4 * envelope * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    pcm16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(pcm16)}h", *pcm16.tolist()))


@unittest.skipUnless(
    _ENABLED, "Set PLAYLIST_CANVAS_TEST_SONARA=1 to run against the real sonara dependency.",
)
class RealSonaraStructureTests(unittest.TestCase):
    def test_structure_analysis_produces_sane_results(self) -> None:
        with TemporaryDirectory(prefix="sonara-real-") as directory:
            duration = 150.0  # 2.5 minutes
            path = Path(directory) / "ramp.wav"
            _write_energy_ramp_wav(path, duration)
            track = PlaylistTrack(str(path), "Ramp", duration_seconds=duration)
            provider = SonaraStructureProvider()

            import threading
            result = provider.analyze(track, cancel_event=threading.Event())

            self.assertEqual(result.analyzer_id, "sonara_structure")
            self.assertGreaterEqual(len(result.sections), 1)
            self.assertGreater(len(result.energy_curve), 0)
            self.assertIsNotNone(result.energy_curve_hop_seconds)
            self.assertGreater(result.energy_curve_hop_seconds, 0.0)
            if result.intro_end_seconds is not None:
                self.assertGreaterEqual(result.intro_end_seconds, 0.0)
                self.assertLessEqual(result.intro_end_seconds, duration)
            if result.outro_start_seconds is not None:
                self.assertGreaterEqual(result.outro_start_seconds, 0.0)
                self.assertLessEqual(result.outro_start_seconds, duration)
            for section in result.sections:
                self.assertLessEqual(section.end_seconds, duration)
                self.assertIsNone(section.label)  # Sonara's segments carry no label

    def test_cache_identity_reflects_the_installed_package(self) -> None:
        import sonara

        provider = SonaraStructureProvider()
        self.assertIn(sonara.__version__, provider.version)


if __name__ == "__main__":
    unittest.main()
