import unittest

from app.renderer.progress_text import audio_progress_text


class AudioProgressTextTests(unittest.TestCase):
    def test_timed_lines_become_clock_times_in_both_languages(self) -> None:
        message = "Measuring loudness 135.2s / 520.0s · 26%"
        self.assertEqual(audio_progress_text(message, True), "최종 믹스 음량 측정 중 · 02:15 / 08:40")
        self.assertEqual(audio_progress_text(message, False), "Measuring the mix loudness · 02:15 / 08:40")
        self.assertEqual(audio_progress_text("Combining mix 12.0s / 3700.0s", True), "믹싱 중 · 00:12 / 01:01:40")

    def test_counted_analysis_lines(self) -> None:
        self.assertEqual(audio_progress_text("Analyzing beats and vocals 6/12", True), "리듬·보컬 분석 중 · 6/12곡 완료")

    def test_other_lines_are_left_to_the_caller(self) -> None:
        self.assertIsNone(audio_progress_text("Combining audio 15.0s / 60.0s · 25%", True))
        self.assertIsNone(audio_progress_text("Encoding 1/2", True))


if __name__ == "__main__":
    unittest.main()
