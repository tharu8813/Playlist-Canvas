from __future__ import annotations

import unittest

from app.preview.adaptive_quality import AdaptivePreviewQuality


class AdaptivePreviewQualityTests(unittest.TestCase):
    def test_sustained_low_fps_reduces_scale_but_one_bad_sample_does_not(self) -> None:
        quality = AdaptivePreviewQuality(0.72)
        first = quality.observe(20.0, 30)
        second = quality.observe(20.0, 30)
        self.assertFalse(first.changed)
        self.assertTrue(second.changed)
        self.assertEqual(second.factor, 0.85)
        self.assertAlmostEqual(second.scale, 0.612)

    def test_stable_samples_recover_one_level_without_flapping(self) -> None:
        quality = AdaptivePreviewQuality(0.65)
        quality.observe(15.0, 30)
        quality.observe(15.0, 30)
        for _ in range(5):
            state = quality.observe(30.0, 30)
            self.assertFalse(state.changed)
        recovered = quality.observe(30.0, 30)
        self.assertTrue(recovered.changed)
        self.assertEqual(recovered.factor, 1.0)

    def test_single_coalesced_drop_does_not_reduce_healthy_preview(self) -> None:
        quality = AdaptivePreviewQuality(0.65)
        for _ in range(4):
            state = quality.observe(30.0, 30, dropped_frames=1)
            self.assertFalse(state.changed)
            self.assertEqual(state.factor, 1.0)

    def test_sustained_drop_pressure_reduces_quality_and_reset_restores_base(self) -> None:
        quality = AdaptivePreviewQuality(0.55)
        quality.observe(30.0, 30, dropped_frames=2)
        reduced = quality.observe(30.0, 30, dropped_frames=2)
        self.assertLess(reduced.scale, 0.55)
        reset = quality.reset(0.72)
        self.assertTrue(reset.changed)
        self.assertEqual(reset.scale, 0.72)


if __name__ == "__main__":
    unittest.main()
