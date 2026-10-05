from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.utils import performance


class PerformanceProfileTests(unittest.TestCase):
    def test_bounded_samples_keep_exact_totals_under_concurrent_analysis(self) -> None:
        profile = performance.PerformanceProfile()
        def record(_: int) -> None:
            for value in range(2000):
                profile.observe("analysis_seconds", float(value))
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(record, range(4)))
        profile.observe("analysis_seconds", float("nan"))
        metric = profile.snapshot()["analysis_seconds"]
        self.assertEqual(metric["count"], 8000)
        self.assertEqual(metric["total"], 4 * sum(range(2000)))
        self.assertEqual(metric["recent_count"], 1024)
        self.assertEqual((metric["min"], metric["max"]), (0, 1999))

    def test_disabled_decorator_has_no_wrapper_and_enabled_one_preserves_outcomes(self) -> None:
        def work(value: int) -> int:
            if value < 0:
                raise ValueError("cancelled")
            return value * 2
        with patch.object(performance, "ENABLED", False):
            self.assertIs(performance.timed("work_seconds")(work), work)
        profile = performance.PerformanceProfile()
        with patch.object(performance, "ENABLED", True), patch.object(performance, "PROFILE", profile):
            measured = performance.timed("work_seconds")(work)
            self.assertEqual(measured(3), 6)
            with self.assertRaisesRegex(ValueError, "cancelled"):
                measured(-1)
        self.assertEqual(profile.snapshot()["work_seconds"]["count"], 2)

    def test_save_writes_inspectable_json_and_io_failure_does_not_break_shutdown(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "profile.json"
            profile = performance.PerformanceProfile()
            profile.observe("decode_seconds", 0.25)
            with patch.object(performance, "ENABLED", True), patch.object(performance, "OUTPUT", str(output)), patch.object(performance, "PROFILE", profile):
                performance.save()
                data = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(data["metrics"]["decode_seconds"]["mean"], 0.25)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])
                with patch.object(Path, "write_text", side_effect=OSError("read only")), self.assertLogs(performance.__name__, level="WARNING"):
                    performance.save()


if __name__ == "__main__":
    unittest.main()
