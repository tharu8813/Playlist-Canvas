"""Cancellation of the loudness pass must not hold Preview shutdown open."""

import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.controllers.progressive_automix_controller import measure_loudness


class ProgressiveLoudnessTests(unittest.TestCase):
    def test_timeout_still_reaps_a_process_that_ignores_termination(self):
        process = MagicMock(args=["ffmpeg"])
        process.__enter__.return_value = process
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired("ffmpeg", 2), ("", "")]
        with patch("subprocess.Popen", return_value=process), patch(
            "app.controllers.progressive_automix_controller.monotonic", side_effect=[0, 301]
        ):
            self.assertEqual(
                measure_loudness(Path("ffmpeg"), "song.wav", threading.Event()),
                (-float("inf"), -float("inf")),
            )
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_cancellation_stops_an_inflight_measurement(self):
        cancel = threading.Event()
        started = threading.Event()
        children = []
        results = []
        popen = subprocess.Popen

        def start_child(_command, **kwargs):
            child = popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            started.set()
            return child

        with patch("subprocess.Popen", side_effect=start_child):
            worker = threading.Thread(
                target=lambda: results.append(measure_loudness(Path("ffmpeg"), "song.wav", cancel))
            )
            worker.start()
            try:
                self.assertTrue(started.wait(5), "measurement never started")
                cancel.set()
                worker.join(3)
                self.assertFalse(worker.is_alive(), "cancelled loudness pass still blocks shutdown")
                self.assertEqual(results, [(-float("inf"), -float("inf"))])
                self.assertIsNotNone(children[0].poll())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait()
                worker.join(5)


if __name__ == "__main__":
    unittest.main()
