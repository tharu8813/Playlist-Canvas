from __future__ import annotations

import csv
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from automix_listening_report import RATING_COLUMNS, write_rating_sheet  # noqa: E402


class RatingSheetTests(unittest.TestCase):
    def test_one_blank_row_per_real_transition(self) -> None:
        rows = [
            {"index": 1, "from": "첫 곡", "to": "B", "timeline_start": 185.4, "duration": 16.0,
             "dsp": "bass_swap", "type": "beat_match"},
            {"index": 2, "from": "B", "to": "C", "timeline_start": 400.0, "duration": 0.0,
             "dsp": "", "type": "sequential"},
            {"index": 3, "from": "C", "to": "D", "timeline_start": 610.0, "duration": 3.0,
             "dsp": "", "type": "crossfade"},
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ratings.csv"
            write_rating_sheet(path, rows)
            with path.open(encoding="utf-8-sig", newline="") as file:
                sheet = list(csv.DictReader(file))

        self.assertEqual([row["index"] for row in sheet], ["1", "3"])  # back-to-back has nothing to rate
        self.assertEqual(sheet[0]["from"], "첫 곡")
        self.assertEqual(sheet[0]["start"], "3:05")
        self.assertEqual(sheet[0]["style"], "bass_swap")
        self.assertEqual(sheet[1]["style"], "crossfade")
        self.assertTrue(all(row[column] == "" for row in sheet for column in RATING_COLUMNS))


if __name__ == "__main__":
    unittest.main()
