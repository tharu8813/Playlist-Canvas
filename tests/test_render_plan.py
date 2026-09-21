from __future__ import annotations

import unittest

from app.models.playlist import PlaylistTrack
from app.timeline.compiler import compile_playlist, compile_timeline
from app.timeline.render_plan import CompiledRenderPlan
from app.timeline.track_schedule import playlist_duration, resolve_track_windows


def _track(duration: float, start: float | None = None, track_id: str | None = None,
           enabled: bool = True) -> PlaylistTrack:
    return PlaylistTrack(
        file_path="x.mp3", title="x", duration_seconds=duration, start_time_seconds=start,
        id=track_id, enabled=enabled,
    )


class RenderPlanCompilerTests(unittest.TestCase):
    def test_sequential_timeline_matches_legacy_windows(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(20.0, track_id="b"), _track(30.0, track_id="c")]
        plan = compile_playlist(tracks)
        self.assertEqual(
            [(c.track_id, c.timeline_start, c.timeline_end) for c in plan.audio.clips],
            [("a", 0.0, 10.0), ("b", 10.0, 30.0), ("c", 30.0, 60.0)],
        )

    def test_gap_is_preserved(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(20.0, start=15.0, track_id="b")]
        plan = compile_playlist(tracks)
        self.assertEqual(
            [(c.track_id, c.timeline_start, c.timeline_end) for c in plan.audio.clips],
            [("a", 0.0, 10.0), ("b", 15.0, 35.0)],
        )

    def test_disabled_track_excluded_when_enabled_only(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(5.0, track_id="b", enabled=False),
                   _track(20.0, track_id="c")]
        plan = compile_playlist(tracks, enabled_only=True)
        self.assertEqual([c.track_id for c in plan.audio.clips], ["a", "c"])
        self.assertEqual(plan.duration_seconds, 30.0)

    def test_empty_timeline_duration_is_zero(self) -> None:
        plan = compile_playlist([])
        self.assertEqual(plan.duration_seconds, 0.0)
        self.assertEqual(plan.audio.clips, ())
        self.assertEqual(plan.presentation.windows, ())
        self.assertEqual(plan.metadata.chapters, ())

    def test_presentation_lookup_and_boundaries(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(20.0, track_id="b")]
        plan = compile_playlist(tracks)
        self.assertEqual(plan.presentation.track_at(5.0), "a")
        self.assertEqual(plan.presentation.local_time(5.0), 5.0)
        self.assertEqual(plan.presentation.track_at(10.0), "b")
        self.assertEqual(plan.presentation.local_time(10.0), 0.0)
        self.assertEqual(plan.presentation.track_at(15.0), "b")
        self.assertEqual(plan.presentation.local_time(15.0), 5.0)
        self.assertEqual(plan.presentation.track_at(1000.0), "b")
        self.assertIsNone(compile_playlist([]).presentation.track_at(0.0))

    def test_metadata_chapters_match_legacy_windows(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(5.0, start=20.0, track_id="b"),
                   _track(8.0, track_id="c")]
        plan = compile_playlist(tracks)
        windows = resolve_track_windows(tracks)
        self.assertEqual(
            [(ch.track_id, ch.start, ch.end) for ch in plan.metadata.chapters],
            [(w.track.id, w.start, w.end) for w in windows],
        )

    def test_timeline_duration_matches_legacy_playlist_duration(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(5.0, start=20.0, track_id="b")]
        plan = compile_playlist(tracks)
        self.assertEqual(plan.duration_seconds, playlist_duration(tracks))

    def test_deterministic_and_no_mutation(self) -> None:
        tracks = [_track(10.0, track_id="a"), _track(20.0, track_id="b")]
        before = [t.to_dict() for t in tracks]
        plan_1 = compile_playlist(tracks)
        plan_2 = compile_playlist(tracks)
        self.assertEqual(plan_1, plan_2)
        self.assertEqual([t.to_dict() for t in tracks], before)

    def test_compile_timeline_does_not_mutate_input(self) -> None:
        tracks = [_track(10.0, track_id="a")]
        from app.timeline.models import timeline_from_playlist
        timeline = timeline_from_playlist(tracks)
        before = timeline
        compile_timeline(timeline)
        self.assertEqual(timeline, before)

    def test_returns_compiled_render_plan_type(self) -> None:
        self.assertIsInstance(compile_playlist([_track(1.0, track_id="a")]), CompiledRenderPlan)


if __name__ == "__main__":
    unittest.main()
