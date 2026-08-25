from __future__ import annotations

import unittest

from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.renderer.export_timeline import ExportTimelinePlanner


class ExportTimelinePlannerTests(unittest.TestCase):
    def test_leading_gap_animation_and_clock_keep_existing_sample_contract(self) -> None:
        track = PlaylistTrack(
            file_path="timing.mp3",
            title="Timing",
            duration_seconds=2.0,
            start_time_seconds=0.5,
        )
        sources = [
            Source(
                SourceType.TEXT,
                "Animated",
                animation_in="fade",
                animation_out="fade",
                animation_in_duration=0.2,
                animation_out_duration=0.2,
            ),
            Source(SourceType.TIME, "Clock", text="%current_time%"),
        ]

        samples = ExportTimelinePlanner.build([track], sources, 15)

        self.assertEqual(len(samples), 11)
        self.assertEqual(
            [sample.animation_phase for sample in samples],
            ["in", "in", "in", "in", None, None, None, None, "out", "out", "out"],
        )
        self.assertAlmostEqual(sum(sample.duration_seconds for sample in samples), 2.5)
        self.assertAlmostEqual(samples[0].duration_seconds, 0.5)
        self.assertAlmostEqual(samples[0].timeline_seconds, 0.0005)
        self.assertEqual(samples[0].animation_progress, 0.0)
        self.assertEqual(samples[1].elapsed_seconds, 0.0)
        self.assertAlmostEqual(samples[-3].elapsed_seconds, 1.8)
        self.assertEqual(samples[3].animation_progress, 1.0)
        self.assertEqual(samples[-1].animation_progress, 1.0)
        stable_elapsed = [
            sample.elapsed_seconds for sample in samples
            if sample.animation_phase is None
        ]
        self.assertEqual(stable_elapsed, [0.2005, 0.5005, 1.0005, 1.5005])

    def test_dynamic_source_boundaries_partition_stable_timeline_without_drift(self) -> None:
        track = PlaylistTrack(
            file_path="boundaries.mp3",
            title="Boundaries",
            duration_seconds=4.0,
            lyrics=[{"start": 0.5, "end": 0.9, "text": "Line"}],
        )
        sources = [
            Source(SourceType.PROGRESS_BAR, "Progress"),
            Source(
                SourceType.LYRICS,
                "Lyrics",
                subtitle_animation="none",
            ),
            Source(
                SourceType.NOW_PLAYING,
                "Now",
                now_playing_duration=3.0,
                now_playing_exit_duration=0.4,
            ),
        ]

        samples = ExportTimelinePlanner.build([track], sources, 10)

        boundaries = [0.0]
        for sample in samples:
            boundaries.append(boundaries[-1] + sample.duration_seconds)
        self.assertAlmostEqual(boundaries[-1], 4.0)
        for expected in (0.5, 0.9, 1.0, 2.0, 2.6, 2.7, 2.8, 2.9, 3.0):
            self.assertTrue(
                any(abs(boundary - expected) < 1e-9 for boundary in boundaries),
                msg=f"missing export boundary {expected}",
            )

    def test_gap_boundaries_keep_previous_track_state_until_next_track(self) -> None:
        first = PlaylistTrack(
            file_path="first.mp3", title="First", duration_seconds=1.0,
        )
        second = PlaylistTrack(
            file_path="second.mp3", title="Second", duration_seconds=1.0,
            start_time_seconds=2.0,
        )
        timed_source = Source(
            SourceType.TEXT,
            "Timed",
            timeline_start=1.4,
            timeline_duration=0.2,
        )

        samples = ExportTimelinePlanner.build([first, second], [timed_source], 30)
        gap_samples = [
            sample for sample in samples
            if 1.0 <= sample.timeline_seconds < 2.0
        ]

        self.assertEqual(len(gap_samples), 3)
        self.assertTrue(all(sample.track is first for sample in gap_samples))
        self.assertTrue(all(sample.track_number == 1 for sample in gap_samples))
        self.assertTrue(all(sample.elapsed_seconds == 1.0 for sample in gap_samples))
        for actual, expected in zip(
            [sample.duration_seconds for sample in gap_samples],
            [0.4, 0.2, 0.4],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(sum(sample.duration_seconds for sample in samples), 3.0)

    def test_sub_millisecond_boundaries_do_not_inflate_timeline_duration(self) -> None:
        track = PlaylistTrack(
            file_path="dense.mp3", title="Dense", duration_seconds=1.0,
        )
        sources = [
            Source(
                SourceType.TEXT, "First", timeline_start=0.5,
                timeline_duration=0.0001,
            ),
            Source(
                SourceType.TEXT, "Second", timeline_start=0.5002,
                timeline_duration=0.0001,
            ),
        ]

        samples = ExportTimelinePlanner.build([track], sources, 30)

        self.assertTrue(all(sample.duration_seconds > 0.0 for sample in samples))
        self.assertAlmostEqual(
            sum(sample.duration_seconds for sample in samples), 1.0,
            places=12,
        )

    def test_sub_millisecond_track_gap_is_preserved(self) -> None:
        first = PlaylistTrack(
            file_path="first.mp3", title="First", duration_seconds=1.0,
        )
        second = PlaylistTrack(
            file_path="second.mp3", title="Second", duration_seconds=1.0,
            start_time_seconds=1.0005,
        )

        samples = ExportTimelinePlanner.build([first, second], [], 30)

        self.assertAlmostEqual(
            sum(sample.duration_seconds for sample in samples), 2.0005,
            places=12,
        )
        self.assertTrue(any(
            abs(sample.duration_seconds - 0.0005) < 1e-12
            for sample in samples
        ))


if __name__ == "__main__":
    unittest.main()
