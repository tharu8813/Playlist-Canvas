import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from app.utils.temp_cleanup import sweep_stale_temp_dirs


class SweepStaleTempDirsTests(unittest.TestCase):
    def test_removes_only_old_app_folders(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            old = time.time() - 3 * 86_400
            for name in ("playlist-video-frames-a", "playlist-preview-audio-b", "other-c"):
                folder = root / name
                folder.mkdir()
                (folder / "x.nut").write_bytes(b"0")
                os.utime(folder, (old, old))
            (root / "playlist-video-live").mkdir()  # fresh: may belong to a running export

            self.assertEqual(sweep_stale_temp_dirs(root=root), 2)
            self.assertEqual(
                sorted(entry.name for entry in root.iterdir()),
                ["other-c", "playlist-video-live"],
            )


if __name__ == "__main__":
    unittest.main()
