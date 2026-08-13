from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.lrc_draft_service import LrcDraftService


class LrcDraftServiceTests(unittest.TestCase):
    def test_audio_scoped_draft_round_trip_and_clear(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            payload = {
                "lines": ["First", "Second"],
                "timestamps": [1.25, None],
                "current_index": 1,
            }
            service = LrcDraftService(root)

            saved = service.save(str(audio), payload)
            loaded = service.load(str(audio))

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.data, payload)
            self.assertEqual(loaded.audio_path, str(audio.resolve()))
            self.assertTrue(saved.path.is_file())

            service.clear(str(audio))
            self.assertIsNone(service.load(str(audio)))


if __name__ == "__main__":
    unittest.main()
