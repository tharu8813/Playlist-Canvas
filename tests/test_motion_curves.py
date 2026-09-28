"""Entrance/exit poses and looping motion shared by Canvas, preview and export."""

from __future__ import annotations

import unittest

from app.animation.curves import (
    ANIMATION_STYLES, AnimationPose, animation_pose, loop_pose, motion_padding,
)
from app.models.source import LOOP_MOTIONS


def _close(first: AnimationPose, second: AnimationPose) -> bool:
    return all(
        abs(a - b) < 1e-6
        for a, b in zip(
            (first.dx, first.dy, first.scale, first.scale_x, first.rotation % 360.0, first.opacity),
            (second.dx, second.dy, second.scale, second.scale_x, second.rotation % 360.0, second.opacity),
        )
    )


class AnimationPoseTests(unittest.TestCase):
    def test_every_style_lands_at_rest_and_leaves_invisible(self) -> None:
        rest = AnimationPose()
        for style in ANIMATION_STYLES:
            with self.subTest(style=style):
                self.assertTrue(_close(animation_pose(style, 1.0, True, 400, 200), rest))
                self.assertTrue(_close(animation_pose(style, 0.0, False, 400, 200), rest))
                if style != "none":
                    self.assertLess(animation_pose(style, 0.0, True, 400, 200).opacity, 0.01)
                    self.assertLess(animation_pose(style, 1.0, False, 400, 200).opacity, 0.01)

    def test_moving_styles_actually_move_midway(self) -> None:
        for style in ANIMATION_STYLES:
            if style in {"none", "fade"}:
                continue
            with self.subTest(style=style):
                pose = animation_pose(style, 0.3, True, 400, 200)
                self.assertFalse(_close(
                    AnimationPose(opacity=pose.opacity), pose,
                ), style)

    def test_bounce_overshoots_and_drop_starts_above(self) -> None:
        self.assertGreater(max(
            animation_pose("bounce", step / 20, True, 400, 200).scale for step in range(21)
        ), 1.0)
        self.assertLess(animation_pose("drop", 0.0, True, 400, 200).dy, 0.0)


class LoopPoseTests(unittest.TestCase):
    def test_loops_repeat_every_period_and_none_is_still(self) -> None:
        for motion in LOOP_MOTIONS:
            with self.subTest(motion=motion):
                first = loop_pose(motion, 1.3, 2.0, 1.0, 300, 300)
                self.assertTrue(_close(first, loop_pose(motion, 3.3, 2.0, 1.0, 300, 300)))
                if motion == "none":
                    self.assertTrue(first.is_identity)
                else:
                    self.assertFalse(_close(first, loop_pose(motion, 1.8, 2.0, 1.0, 300, 300)))
        self.assertTrue(loop_pose("float", 1.0, 2.0, 0.0, 300, 300).is_identity)

    def test_padding_grows_for_moving_sources(self) -> None:
        still = motion_padding({"none"}, "none", 1.0, 300, 300)
        self.assertEqual(still, 0.0)
        self.assertGreater(motion_padding({"drop"}, "none", 1.0, 300, 300), still)
        self.assertGreater(motion_padding({"none"}, "spin", 1.0, 300, 300), 100.0)


if __name__ == "__main__":
    unittest.main()
