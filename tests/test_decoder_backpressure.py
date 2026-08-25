from __future__ import annotations

import unittest

from app.video.decoder_backpressure import VideoDecoderBackpressure


class VideoDecoderBackpressureTests(unittest.TestCase):
    def test_queue_pressure_reduces_resolution_before_fps(self) -> None:
        pressure = VideoDecoderBackpressure(30)

        first = pressure.observe(
            accepted_frames=12, pressure_drops=1, pending_decoders=1,
            active_decoders=1,
        )
        second = pressure.observe(
            accepted_frames=12, pressure_drops=1, pending_decoders=1,
            active_decoders=1,
        )

        self.assertFalse(first.changed)
        self.assertTrue(second.changed)
        self.assertEqual(second.resolution_factor, 0.82)
        self.assertEqual(second.fps_limit, 30)

    def test_canvas_cadence_does_not_change_decoder_budgets(self) -> None:
        pressure = VideoDecoderBackpressure(30)

        for _ in range(6):
            state = pressure.observe(
                accepted_frames=10, pressure_drops=0, pending_decoders=0,
                active_decoders=1,
            )
            self.assertFalse(state.changed)

        self.assertEqual(state.resolution_factor, 1.0)
        self.assertEqual(state.fps_limit, 30)

    def test_persistent_pressure_eventually_reduces_both_budgets(self) -> None:
        pressure = VideoDecoderBackpressure(30)

        states = [
            pressure.observe(
                accepted_frames=10, pressure_drops=1, pending_decoders=1,
                active_decoders=1,
            )
            for _ in range(7)
        ]

        self.assertLess(states[-1].resolution_factor, 0.82)
        self.assertLess(states[-1].fps_limit, 30)

    def test_resolution_and_fps_recover_with_separate_hysteresis(self) -> None:
        pressure = VideoDecoderBackpressure(30)
        for _ in range(7):
            pressure.observe(
                accepted_frames=10, pressure_drops=1, pending_decoders=1,
                active_decoders=1,
            )
        reduced_resolution = pressure.resolution_factor
        reduced_fps = pressure.fps_limit

        for _ in range(8):
            state = pressure.observe(
                accepted_frames=10, pressure_drops=0, pending_decoders=0,
                active_decoders=1,
            )

        self.assertGreater(state.resolution_factor, reduced_resolution)
        self.assertEqual(state.fps_limit, reduced_fps)
        for _ in range(4):
            state = pressure.observe(
                accepted_frames=10, pressure_drops=0, pending_decoders=0,
                active_decoders=1,
            )
        self.assertGreater(state.fps_limit, reduced_fps)


if __name__ == "__main__":
    unittest.main()
