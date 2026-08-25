from __future__ import annotations

import unittest

from app.models.source import Source, SourceType
from app.video.timeline import build_video_occurrences, resolve_video_position


class VideoTimelineTests(unittest.TestCase):
    def test_once_becomes_transparent_after_first_clip(self) -> None:
        source = Source(SourceType.VIDEO, "Clip", video_paths=["a.mp4"])
        durations = {"a.mp4": 2.0}
        self.assertEqual(resolve_video_position(source, source.video_paths, durations, 1.0).path, "a.mp4")
        self.assertIsNone(resolve_video_position(source, source.video_paths, durations, 2.0))

    def test_sequence_cycle_limit_is_respected(self) -> None:
        source = Source(
            SourceType.VIDEO, "Sequence", video_paths=["a.mp4", "b.mp4"],
            video_repeat_mode="sequence", video_cycle_count=2,
        )
        durations = {"a.mp4": 1.0, "b.mp4": 2.0}
        occurrences = build_video_occurrences(
            source, source.video_paths, durations, 5.0, 20.0,
        )
        self.assertEqual([item.path for item in occurrences], ["a.mp4", "b.mp4"] * 2)
        self.assertAlmostEqual(sum(item.duration_seconds for item in occurrences), 6.0)
        self.assertEqual(occurrences[0].timeline_start, 5.0)

    def test_random_order_is_deterministic_for_preview_and_export(self) -> None:
        source = Source(
            SourceType.VIDEO, "Random", video_paths=["a", "b", "c"],
            video_repeat_mode="random", video_cycle_count=3,
            video_random_seed=912,
        )
        durations = {path: 1.0 for path in source.video_paths}
        first = build_video_occurrences(source, source.video_paths, durations, 0.0, 9.0)
        second = build_video_occurrences(source, source.video_paths, durations, 0.0, 9.0)
        self.assertEqual(first, second)
        for second_index in range(9):
            position = resolve_video_position(
                source, source.video_paths, durations, second_index + 0.1,
            )
            self.assertIsNotNone(position)
            self.assertEqual(position.path, first[second_index].path)

    def test_speed_changes_output_duration_but_keeps_media_time(self) -> None:
        source = Source(
            SourceType.VIDEO, "Fast", video_paths=["a"],
            video_repeat_mode="once", video_speed=2.0,
        )
        durations = {"a": 8.0}
        occurrences = build_video_occurrences(source, ["a"], durations, 0.0, 10.0)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].duration_seconds, 4.0)
        self.assertEqual(resolve_video_position(source, ["a"], durations, 3.0).seconds, 6.0)


if __name__ == "__main__":
    unittest.main()
