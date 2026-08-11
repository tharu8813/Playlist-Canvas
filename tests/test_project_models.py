from __future__ import annotations

import unittest

from app.models.playlist import PlaylistTrack
from app.models.project import CanvasSettings, ProjectDocument
from app.models.source import Source, SourceType


class ProjectModelValidationTests(unittest.TestCase):
    def test_valid_document_round_trip(self) -> None:
        document = ProjectDocument(
            sources=[Source(SourceType.TEXT, "Title", text="%title%")],
            playlist=[PlaylistTrack(
                "song.mp3", "Song", duration_seconds=12.5,
                lyrics_timing_offset_seconds=0.35,
            )],
            canvas=CanvasSettings(width=1920, height=1080, snap_enabled=False),
        )
        restored = ProjectDocument.from_dict(document.to_dict())
        self.assertEqual(restored.canvas.width, 1920)
        self.assertFalse(restored.canvas.snap_enabled)
        self.assertEqual(restored.sources[0].text, "%title%")
        self.assertEqual(restored.playlist[0].lyrics_timing_offset_seconds, 0.35)

    def test_duplicate_source_ids_are_rejected(self) -> None:
        first = Source(SourceType.TEXT, "First")
        second = Source(SourceType.TEXT, "Second", id=first.id)
        payload = ProjectDocument(sources=[first, second]).to_dict()
        with self.assertRaisesRegex(ValueError, "source IDs must be unique"):
            ProjectDocument.from_dict(payload)

    def test_invalid_canvas_and_non_finite_source_are_rejected(self) -> None:
        payload = ProjectDocument().to_dict()
        payload["canvas"]["width"] = -1
        with self.assertRaisesRegex(ValueError, "Canvas dimensions"):
            ProjectDocument.from_dict(payload)

        payload = ProjectDocument(
            sources=[Source(SourceType.TEXT, "Invalid")]
        ).to_dict()
        payload["sources"][0]["x"] = float("nan")
        with self.assertRaisesRegex(ValueError, "invalid numeric value"):
            ProjectDocument.from_dict(payload)

        payload = ProjectDocument(
            sources=[Source(SourceType.PARTICLE_OVERLAY, "Too many particles")]
        ).to_dict()
        payload["sources"][0]["particle_density"] = 1_000_000
        with self.assertRaisesRegex(ValueError, "particle_density"):
            ProjectDocument.from_dict(payload)

    def test_invalid_track_timing_is_rejected(self) -> None:
        payload = ProjectDocument(
            playlist=[PlaylistTrack("song.mp3", "Song")]
        ).to_dict()
        payload["playlist"][0]["start_time_seconds"] = -1
        with self.assertRaisesRegex(ValueError, "invalid start time"):
            ProjectDocument.from_dict(payload)

        payload = ProjectDocument(
            playlist=[PlaylistTrack("song.mp3", "Song")]
        ).to_dict()
        payload["playlist"][0]["lyrics_timing_offset_seconds"] = float("nan")
        with self.assertRaisesRegex(ValueError, "lyric timing offset"):
            ProjectDocument.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
