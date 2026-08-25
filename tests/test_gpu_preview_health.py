from __future__ import annotations

import unittest

from app.preview.gpu_health import GpuPreviewHealth


class GpuPreviewHealthTests(unittest.TestCase):
    def test_single_stall_retries_but_second_consecutive_stall_trips(self) -> None:
        health = GpuPreviewHealth(fallback_after_stalls=2)
        health.frame_queued(1.0)
        self.assertFalse(health.frame_timed_out())
        health.frame_queued(2.0)
        self.assertTrue(health.frame_timed_out())
        self.assertEqual(health.stats.total_stalls, 2)

    def test_presented_frame_clears_consecutive_stalls_and_records_latency(self) -> None:
        health = GpuPreviewHealth(fallback_after_stalls=2)
        health.frame_queued(1.0)
        health.frame_timed_out()
        health.frame_queued(2.0)
        health.frame_presented(2.025)
        self.assertEqual(health.stats.consecutive_stalls, 0)
        self.assertAlmostEqual(health.stats.last_latency_ms, 25.0)
        health.frame_queued(3.0)
        self.assertFalse(health.frame_timed_out())

    def test_cancelled_hidden_frame_does_not_count_as_stall(self) -> None:
        health = GpuPreviewHealth()
        health.frame_queued(1.0)
        health.cancel_wait()
        self.assertFalse(health.frame_timed_out())
        self.assertEqual(health.stats.total_stalls, 0)


if __name__ == "__main__":
    unittest.main()
