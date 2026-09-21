"""Phase 8 Frame Evaluation Layer: resolve_lyrics_cue_state() is the shared
lyric cue/transition resolver used by both CanvasSnapshot.capture_track
(preview/export painting) and ExportCanvasCapturer._source_state_key
(export frame coalescing). These tests check it resolves the same input to
the same FrameState deterministically, and matches the specific cue/
transition math both call sites relied on before the extraction."""

import unittest

from app.models.playlist import PlaylistTrack
from app.preview.frame_state import resolve_lyrics_cue_state


def _track(lyrics: list[dict[str, object]], offset: float = 0.0) -> PlaylistTrack:
    return PlaylistTrack(
        file_path="song.mp3", title="Song", duration_seconds=180.0,
        lyrics=lyrics, lyrics_timing_offset_seconds=offset,
    )


class FrameStateTests(unittest.TestCase):
    def test_same_input_produces_same_state(self) -> None:
        track = _track([
            {"start": 0.0, "text": "first"},
            {"start": 2.0, "text": "second"},
        ])
        first = resolve_lyrics_cue_state(track, 2.5, 0.0, 0.0, "glow", 0.4)
        second = resolve_lyrics_cue_state(track, 2.5, 0.0, 0.0, "glow", 0.4)
        self.assertEqual(first, second)

    def test_no_lyrics_resolves_to_no_cue(self) -> None:
        state = resolve_lyrics_cue_state(_track([]), 5.0, 0.0, 0.0, "glow", 0.4)
        self.assertIsNone(state.cue_index)
        self.assertIsNone(state.active_cue_index)
        self.assertFalse(state.transitioning)

    def test_transition_progresses_over_the_animation_duration(self) -> None:
        track = _track([{"start": 10.0, "text": "line"}])
        just_started = resolve_lyrics_cue_state(track, 10.0, 0.0, 0.0, "glow", 0.5)
        self.assertTrue(just_started.transitioning)
        self.assertAlmostEqual(just_started.transition_progress, 0.0)

        halfway = resolve_lyrics_cue_state(track, 10.25, 0.0, 0.0, "glow", 0.5)
        self.assertAlmostEqual(halfway.transition_progress, 0.5)

        # A single cue has no successor to hand off to, so `transitioning`
        # (== "this is the active cue") stays true; progress just clamps at 1.0.
        finished = resolve_lyrics_cue_state(track, 11.0, 0.0, 0.0, "glow", 0.5)
        self.assertTrue(finished.transitioning)
        self.assertEqual(finished.transition_progress, 1.0)

    def test_animation_none_never_transitions(self) -> None:
        track = _track([{"start": 0.0, "text": "line"}])
        state = resolve_lyrics_cue_state(track, 0.0, 0.0, 0.0, "none", 0.5)
        self.assertFalse(state.transitioning)
        self.assertEqual(state.transition_progress, 1.0)

    def test_timing_offsets_shift_the_effective_elapsed_time(self) -> None:
        track = _track([{"start": 5.0, "text": "line"}], offset=2.0)
        # A +2s track offset plus a +1s per-source offset means elapsed=2.0
        # lines up with the cue at start=5.0 (2.0 + 2.0 + 1.0 = 5.0).
        state = resolve_lyrics_cue_state(track, 2.0, 2.0, 1.0, "glow", 0.5)
        self.assertEqual(state.cue_index, 0)
        self.assertTrue(state.transitioning)
        self.assertAlmostEqual(state.transition_progress, 0.0)


if __name__ == "__main__":
    unittest.main()
