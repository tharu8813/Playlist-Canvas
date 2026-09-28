"""Background work yields the CPU to Preview; everything else starts at normal priority."""

from __future__ import annotations

import subprocess
import sys
import threading
import unittest

from app.utils.subprocess_utils import background_work, background_work_active, hidden_process_kwargs


@unittest.skipUnless(sys.platform == "win32", "Windows priority classes")
class BackgroundWorkTests(unittest.TestCase):
    def test_helpers_start_below_normal_only_inside_the_scope(self) -> None:
        below = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        self.assertFalse(hidden_process_kwargs()["creationflags"] & below)
        with background_work():
            self.assertTrue(background_work_active())
            self.assertTrue(hidden_process_kwargs()["creationflags"] & below)
            # Other threads (analysis pools, loudness scans) see it too.
            seen: list[int] = []
            thread = threading.Thread(target=lambda: seen.append(hidden_process_kwargs()["creationflags"]))
            thread.start()
            thread.join()
            self.assertTrue(seen[0] & below)
        self.assertFalse(background_work_active())
        self.assertFalse(hidden_process_kwargs()["creationflags"] & below)
        self.assertTrue(hidden_process_kwargs()["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_nested_scopes_and_errors_unwind(self) -> None:
        with self.assertRaises(RuntimeError):
            with background_work():
                with background_work():
                    raise RuntimeError("render failed")
        self.assertFalse(background_work_active())


if __name__ == "__main__":
    unittest.main()
