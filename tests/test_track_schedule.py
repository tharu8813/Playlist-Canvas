from __future__ import annotations

import unittest

from app.models.playlist import PlaylistTrack
from app.timeline.track_schedule import playlist_duration, resolve_track_windows


def _track(duration: float, start: float | None = None) -> PlaylistTrack:
    return PlaylistTrack(
        file_path="x.mp3", title="x", duration_seconds=duration, start_time_seconds=start,
    )


class TrackScheduleTests(unittest.TestCase):
    def test_sequential_tracks_have_no_gaps(self) -> None:
        tracks = [_track(10.0), _track(5.0), _track(8.0)]
        windows = resolve_track_windows(tracks)
        self.assertEqual([(w.floor, w.start, w.end) for w in windows], [
            (0.0, 0.0, 10.0), (10.0, 10.0, 15.0), (15.0, 15.0, 23.0),
        ])

    def test_explicit_start_creates_a_gap(self) -> None:
        tracks = [_track(10.0), _track(5.0, start=20.0)]
        windows = resolve_track_windows(tracks)
        self.assertEqual(windows[1].floor, 10.0)
        self.assertEqual(windows[1].start, 20.0)
        self.assertEqual(windows[1].end, 25.0)

    def test_explicit_start_earlier_than_floor_is_clamped_up(self) -> None:
        tracks = [_track(10.0), _track(5.0, start=2.0)]
        windows = resolve_track_windows(tracks)
        self.assertEqual(windows[1].start, 10.0)
        self.assertEqual(windows[1].end, 15.0)

    def test_empty_playlist_duration_is_zero(self) -> None:
        self.assertEqual(playlist_duration([]), 0.0)

    def test_playlist_duration_matches_last_window_end(self) -> None:
        tracks = [_track(10.0), _track(5.0, start=20.0)]
        self.assertEqual(playlist_duration(tracks), 25.0)


if __name__ == "__main__":
    unittest.main()
