from __future__ import annotations

import unittest

from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.renderer.export_timeline import ExportTimelinePlanner


class ExportTimelinePlannerTests(unittest.TestCase):
    def test_progress_motion_uses_every_selected_output_frame(self) -> None:
        track = PlaylistTrack(
            file_path="progress.mp3", title="Progress", duration_seconds=1.0,
        )
        progress = Source(SourceType.PROGRESS_BAR, "Progress")

        samples_30 = ExportTimelinePlanner.build([track], [progress], 30)
        samples_60 = ExportTimelinePlanner.build([track], [progress], 60)

        self.assertEqual(len(samples_30), 30)
        self.assertEqual(len(samples_60), 60)
        self.assertTrue(all(
            abs(sample.duration_seconds - 1 / 30) < 1e-12
            for sample in samples_30
        ))
        self.assertTrue(all(
            abs(sample.duration_seconds - 1 / 60) < 1e-12
            for sample in samples_60
        ))

    def test_ambient_album_background_gets_a_full_flow_rate_schedule(self) -> None:
        from app.preview.album_art import AMBIENT_FLOW_HZ

        track = PlaylistTrack(
            file_path="ambient.mp3", title="Ambient", duration_seconds=2.0,
        )
        ambient_bg = Source(
            SourceType.BACKGROUND, "Cover",
            background_mode="album_art", background_ambient=True,
        )
        static_bg = Source(
            SourceType.BACKGROUND, "Solid", background_mode="album_art",
        )

        flowing = ExportTimelinePlanner.build([track], [ambient_bg], 30)
        frozen = ExportTimelinePlanner.build([track], [static_bg], 30)

        self.assertEqual(len(frozen), 1)
        self.assertEqual(len(flowing), round(2.0 * AMBIENT_FLOW_HZ))
        self.assertAlmostEqual(
            sum(sample.duration_seconds for sample in flowing), 2.0, places=12,
        )
        elapsed = [round(sample.elapsed_seconds, 4) for sample in flowing]
        self.assertEqual(len(set(elapsed)), len(elapsed))

    def test_album_background_transition_densifies_later_track_openings(self) -> None:
        first = PlaylistTrack(
            file_path="first.mp3", title="First", duration_seconds=3.0,
        )
        second = PlaylistTrack(
            file_path="second.mp3", title="Second", duration_seconds=3.0,
        )
        bg = Source(
            SourceType.BACKGROUND, "Cover", background_mode="album_art",
            background_track_transition=True,
            background_track_transition_seconds=0.8,
        )

        with_transition = ExportTimelinePlanner.build([first, second], [bg], 30)
        bg.background_track_transition = False
        without = ExportTimelinePlanner.build([first, second], [bg], 30)

        self.assertGreater(len(with_transition), len(without))
        fade_frames = [
            sample for sample in with_transition
            if sample.track_number == 2 and sample.elapsed_seconds < 0.8 + 1e-6
        ]
        self.assertGreaterEqual(len(fade_frames), round(0.8 * 30) - 1)
        # The first track has no previous artwork, so it is never densified.
        self.assertEqual(
            len([s for s in with_transition if s.track_number == 1]),
            len([s for s in without if s.track_number == 1]),
        )

    def test_ambient_album_background_flows_through_track_gaps(self) -> None:
        from app.preview.album_art import AMBIENT_FLOW_HZ

        first = PlaylistTrack(
            file_path="first.mp3", title="First", duration_seconds=1.0,
        )
        second = PlaylistTrack(
            file_path="second.mp3", title="Second", duration_seconds=1.0,
            start_time_seconds=2.0,
        )
        ambient_bg = Source(
            SourceType.BACKGROUND, "Cover",
            background_mode="album_art", background_ambient=True,
        )

        samples = ExportTimelinePlanner.build([first, second], [ambient_bg], 30)
        gap_samples = [
            sample for sample in samples
            if 1.0 <= sample.timeline_seconds < 2.0
        ]

        self.assertEqual(len(gap_samples), round(1.0 * AMBIENT_FLOW_HZ))
        self.assertTrue(all(sample.track is first for sample in gap_samples))
        self.assertAlmostEqual(
            sum(sample.duration_seconds for sample in samples), 3.0, places=12,
        )

    def test_element_animations_scale_with_selected_output_frame_rate(self) -> None:
        track = PlaylistTrack(
            file_path="animation.mp3", title="Animation", duration_seconds=4.0,
        )
        animated = Source(
            SourceType.SHAPE,
            "Animated",
            animation_in="fade",
            animation_out="slide_up",
            animation_in_duration=1.0,
            animation_out_duration=1.0,
        )

        samples_30 = ExportTimelinePlanner.build([track], [animated], 30)
        samples_60 = ExportTimelinePlanner.build([track], [animated], 60)
        intro_30 = [sample for sample in samples_30 if sample.animation_phase == "in"]
        intro_60 = [sample for sample in samples_60 if sample.animation_phase == "in"]
        outro_30 = [sample for sample in samples_30 if sample.animation_phase == "out"]
        outro_60 = [sample for sample in samples_60 if sample.animation_phase == "out"]

        self.assertEqual(len(intro_30), 30)
        self.assertEqual(len(intro_60), 60)
        self.assertEqual(len(outro_30), 30)
        self.assertEqual(len(outro_60), 60)
        self.assertEqual(intro_30[0].animation_progress, 0.0)
        self.assertEqual(intro_60[0].animation_progress, 0.0)
        self.assertEqual(intro_30[-1].animation_progress, 1.0)
        self.assertEqual(intro_60[-1].animation_progress, 1.0)
        self.assertEqual(intro_30[-1].elapsed_seconds, 1.0)
        self.assertEqual(intro_60[-1].elapsed_seconds, 1.0)
        self.assertEqual(outro_30[-1].animation_progress, 1.0)
        self.assertEqual(outro_60[-1].animation_progress, 1.0)
        self.assertEqual(outro_30[-1].elapsed_seconds, track.duration_seconds)
        self.assertEqual(outro_60[-1].elapsed_seconds, track.duration_seconds)

    def test_lyrics_and_now_playing_transitions_follow_output_frame_rate(self) -> None:
        track = PlaylistTrack(
            file_path="transitions.mp3",
            title="Transitions",
            duration_seconds=2.0,
            lyrics=[{"start": 0.25, "end": 0.9, "text": "Line"}],
        )
        sources = [
            Source(
                SourceType.LYRICS,
                "Lyrics",
                subtitle_animation="glow",
                subtitle_animation_duration=0.5,
            ),
            Source(
                SourceType.NOW_PLAYING,
                "Now playing",
                now_playing_duration=1.75,
                now_playing_exit_animation="fade",
                now_playing_exit_duration=0.5,
            ),
        ]

        samples_30 = ExportTimelinePlanner.build([track], sources, 30)
        samples_60 = ExportTimelinePlanner.build([track], sources, 60)
        transition_frames_30 = sum(
            abs(sample.duration_seconds - 1 / 30) < 1e-12
            for sample in samples_30
        )
        transition_frames_60 = sum(
            abs(sample.duration_seconds - 1 / 60) < 1e-12
            for sample in samples_60
        )

        self.assertEqual(transition_frames_30, 30)
        self.assertEqual(transition_frames_60, 60)

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
