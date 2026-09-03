from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.lyrics_service import find_sidecar_lyrics


class FindSidecarLyricsTests(unittest.TestCase):
    def _folder(self, *names: str) -> Path:
        directory = Path(self._tmp.name)
        for name in names:
            (directory / name).write_text("x", encoding="utf-8")
        return directory

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory(prefix="pc-sidecar-")
        self.addCleanup(self._tmp.cleanup)

    def test_exact_stem_match_is_reported_as_exact_and_prefers_lrc(self) -> None:
        folder = self._folder("Song.mp3", "Song.srt", "Song.lrc", "Other.lrc")
        exact, similar = find_sidecar_lyrics(folder / "Song.mp3")

        self.assertEqual([s.path.name for s in exact], ["Song.lrc", "Song.srt"])
        self.assertTrue(all(s.exact for s in exact))
        self.assertEqual(similar, [])

    def test_track_number_prefix_and_copy_suffix_count_as_similar(self) -> None:
        folder = self._folder("01 - Golden Hour.mp3", "Golden Hour (1).lrc")
        exact, similar = find_sidecar_lyrics(folder / "01 - Golden Hour.mp3")

        self.assertEqual(exact, [])
        self.assertEqual([s.path.name for s in similar], ["Golden Hour (1).lrc"])
        self.assertFalse(similar[0].exact)

    def test_unrelated_lyric_files_are_ignored(self) -> None:
        folder = self._folder("Aurora.mp3", "Completely Different.lrc")
        exact, similar = find_sidecar_lyrics(folder / "Aurora.mp3")

        self.assertEqual(exact, [])
        self.assertEqual(similar, [])

    def test_missing_folder_returns_empty(self) -> None:
        exact, similar = find_sidecar_lyrics(Path("/no/such/place/song.mp3"))
        self.assertEqual((exact, similar), ([], []))


if __name__ == "__main__":
    unittest.main()
