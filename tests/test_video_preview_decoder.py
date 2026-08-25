from __future__ import annotations

import unittest

from app.video.preview_decoder import (
    video_position_needs_seek, video_seek_tolerance_ms,
)


class VideoPreviewDecoderTests(unittest.TestCase):
    def test_seek_tolerance_is_bounded_for_preview_frame_rates(self) -> None:
        self.assertEqual(video_seek_tolerance_ms(10), 300)
        self.assertEqual(video_seek_tolerance_ms(30), 160)
        self.assertEqual(video_seek_tolerance_ms(60), 160)

    def test_small_decoder_clock_drift_does_not_trigger_seek(self) -> None:
        self.assertFalse(video_position_needs_seek(1_000, 1_150, 30))
        self.assertTrue(video_position_needs_seek(1_000, 1_200, 30))
        self.assertTrue(video_position_needs_seek(-1, 0, 30))


if __name__ == "__main__":
    unittest.main()
