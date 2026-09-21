from __future__ import annotations

from dataclasses import replace
import unittest

from app.models.playlist import PlaylistTrack
from app.models.project import ProjectDocument
from app.timeline.models import (
    AudioClip, AudioTrack, AudioTransition, Timeline, TransitionType,
    timeline_from_playlist,
)
from app.timeline.track_schedule import resolve_track_windows


class TimelineModelTests(unittest.TestCase):
    def test_legacy_schedule_disabled_filter_and_stable_ids(self) -> None:
        tracks = [
            PlaylistTrack("a.wav", "A", duration_seconds=10, id="a"),
            PlaylistTrack("b.wav", "B", duration_seconds=5, id="b", enabled=False),
            PlaylistTrack("c.wav", "C", duration_seconds=2, id="c", start_time_seconds=1),
            PlaylistTrack("d.wav", "D", duration_seconds=0, id="d", start_time_seconds=30),
        ]
        before = [track.to_dict() for track in tracks]
        for enabled_only in (False, True):
            with self.subTest(enabled_only=enabled_only):
                timeline = timeline_from_playlist(tracks, enabled_only=enabled_only)
                selected = [t for t in tracks if t.enabled or not enabled_only]
                expected = resolve_track_windows(selected)
                clips = timeline.audio_tracks[0].clips
                self.assertEqual([(c.timeline_start, c.timeline_end) for c in clips],
                                 [(w.start, w.end) for w in expected])
                self.assertEqual([c.track_id for c in clips], [t.id for t in selected])
                self.assertEqual([c.enabled for c in clips], [t.enabled for t in selected])
                self.assertEqual(timeline.duration, 30)
                self.assertEqual(timeline, timeline_from_playlist(tracks, enabled_only=enabled_only))
        self.assertEqual(timeline_from_playlist(tracks).audio_tracks[0].clips[2].timeline_start, 15)
        self.assertEqual(timeline_from_playlist(tracks, enabled_only=True).audio_tracks[0].clips[1].timeline_start, 10)
        original_ids = {c.track_id: c.id for c in timeline_from_playlist(tracks).audio_tracks[0].clips}
        reordered_ids = {c.track_id: c.id for c in timeline_from_playlist(tracks[::-1]).audio_tracks[0].clips}
        self.assertEqual(original_ids, reordered_ids)
        self.assertEqual(before, [track.to_dict() for track in tracks])

    def test_overlap_trim_rate_crossfade_and_multiple_lanes(self) -> None:
        a = AudioClip("a", "media", 0, 2, 22, playback_rate=2, gain=0.5)
        b = AudioClip("b", "media", 8, 0, 10)
        transition = AudioTransition("a", "b", 8, 2, TransitionType.EQUAL_POWER)
        timeline = Timeline((AudioTrack("one", (b, a)),), (transition,))
        self.assertEqual(a.duration, 10)
        self.assertEqual(a.timeline_end, 10)
        self.assertEqual(timeline.duration, 18)
        self.assertEqual(timeline.transitions[0].duration, a.timeline_end - b.timeline_start)
        self.assertEqual(Timeline((AudioTrack("one", (a,)), AudioTrack("two", (b,)))).duration, 18)
        self.assertEqual(Timeline().duration, 0)
        self.assertEqual(timeline_from_playlist([]).duration, 0)

    def test_legacy_projects_round_trip_without_new_storage_fields(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version):
                document = ProjectDocument.from_dict({"version": version, "playlist": [
                    PlaylistTrack("a.wav", "A", duration_seconds=4, id="a").to_dict(),
                ]})
                saved = document.to_dict()
                self.assertNotIn("timeline", saved)
                self.assertEqual(saved["version"], 2)
                self.assertEqual(document.timeline.duration, 4)
                self.assertEqual(ProjectDocument.from_dict(saved).timeline, document.timeline)
                self.assertEqual(document.to_dict(), saved)
                snapshot = document.timeline
                document.playlist[0].duration_seconds = 7
                self.assertEqual(document.timeline.duration, 7)
                self.assertEqual(snapshot.duration, 4)

    def test_invalid_clip_values_and_transition_references(self) -> None:
        clip = AudioClip("a", "media", 0, 0, 10)
        for change in (
            {"playback_rate": 0}, {"playback_rate": -1}, {"source_in": 11},
            {"source_out": float("inf")}, {"timeline_start": float("nan")},
            {"gain": -1}, {"gain": True}, {"enabled": 1}, {"id": ""},
            {"source_out": 1e308, "playback_rate": 1e-308},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(clip, **change)
        with self.assertRaises(ValueError):
            Timeline((AudioTrack("lane", (clip, clip)),))
        with self.assertRaises(ValueError):
            Timeline((AudioTrack("lane", (clip,)),), (AudioTransition("a", "missing", 0, 1),))
        for change in ({"duration": -1}, {"start": float("nan")}, {"clip_b": "a"}, {"type": "unknown"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(AudioTransition("a", "b", 0, 1), **change)


if __name__ == "__main__":
    unittest.main()
