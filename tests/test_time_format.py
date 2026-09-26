import unittest

from app.utils.time_format import format_clock


class FormatClockTests(unittest.TestCase):
    def test_minutes_and_seconds_below_one_hour(self) -> None:
        self.assertEqual(format_clock(0), "00:00")
        self.assertEqual(format_clock(125), "02:05")
        self.assertEqual(format_clock(3599), "59:59")

    def test_hours_are_split_out_from_one_hour_on(self) -> None:
        self.assertEqual(format_clock(3600), "01:00:00")
        self.assertEqual(format_clock(3661), "01:01:01")

    def test_hours_false_keeps_counting_minutes(self) -> None:
        self.assertEqual(format_clock(3599, hours=False), "59:59")
        self.assertEqual(format_clock(4500, hours=False), "75:00")

    def test_negative_input_clamps_to_zero(self) -> None:
        self.assertEqual(format_clock(-1), "00:00")
        self.assertEqual(format_clock(-7200, hours=False), "00:00")


if __name__ == "__main__":
    unittest.main()
