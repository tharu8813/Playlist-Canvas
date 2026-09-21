from __future__ import annotations

import unittest

from app.automix.models import TrackAnalysis


def _analysis(**overrides) -> TrackAnalysis:
    fields = dict(
        track_id="a", source_path="a.mp3", duration_seconds=180.0,
        bpm=120.0, bpm_confidence=0.9, beats=(0.5, 1.0, 1.5), downbeats=(0.5,),
        meter_numerator=4, meter_denominator=4, key="A minor", key_confidence=0.5,
        energy=0.7, vocal_activity=((1.0, 2.0), (3.0, 4.0)),
        analyzer_id="basic", analyzer_version="1",
    )
    fields.update(overrides)
    return TrackAnalysis(**fields)


class TrackAnalysisModelTests(unittest.TestCase):
    def test_valid_analysis_round_trips_through_cache_fields(self) -> None:
        analysis = _analysis()
        restored = TrackAnalysis.from_cache_fields(
            analysis.track_id, analysis.source_path, analysis.to_cache_fields(),
        )
        self.assertEqual(analysis, restored)

    def test_minimal_analysis_needs_only_identity_and_duration(self) -> None:
        analysis = TrackAnalysis(track_id="a", source_path="a.mp3", duration_seconds=10.0)
        self.assertIsNone(analysis.bpm)
        self.assertEqual(analysis.beats, ())

    def test_negative_bpm_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(bpm=-1.0)

    def test_zero_bpm_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(bpm=0.0)

    def test_confidence_out_of_range_is_invalid(self) -> None:
        for change in ({"bpm_confidence": 1.5}, {"bpm_confidence": -0.1}, {"key_confidence": 2.0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                _analysis(**change)

    def test_unsorted_beats_are_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(beats=(1.0, 0.5))

    def test_beat_beyond_duration_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(duration_seconds=10.0, beats=(11.0,))

    def test_negative_beat_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(beats=(-0.5,))

    def test_empty_track_id_or_source_path_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(track_id="")
        with self.assertRaises(ValueError):
            _analysis(source_path="")

    def test_negative_duration_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(duration_seconds=-1.0)

    def test_overlapping_vocal_activity_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(vocal_activity=((1.0, 3.0), (2.0, 4.0)))

    def test_inverted_vocal_activity_span_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _analysis(vocal_activity=((3.0, 1.0),))

    def test_analysis_is_frozen(self) -> None:
        analysis = _analysis()
        with self.assertRaises(Exception):
            analysis.bpm = 100.0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
