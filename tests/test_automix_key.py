from __future__ import annotations

import unittest

from app.automix.analysis.key import (
    PITCH_CLASSES,
    _rotate,
    camelot_compatible,
    estimate_key,
    key_to_camelot,
)


class EstimateKeyTests(unittest.TestCase):
    def test_rotated_major_profile_is_recovered_exactly(self) -> None:
        major_profile = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
        for root in range(12):
            chroma = _rotate(major_profile, root)
            key, confidence = estimate_key(chroma)
            with self.subTest(root=root):
                self.assertEqual(key, f"{PITCH_CLASSES[root]} major")
                self.assertGreater(confidence, 0.99)

    def test_rotated_minor_profile_is_recovered_exactly(self) -> None:
        minor_profile = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
        chroma = _rotate(minor_profile, 9)  # A minor
        key, confidence = estimate_key(chroma)
        self.assertEqual(key, "A minor")
        self.assertGreater(confidence, 0.99)

    def test_silent_chroma_returns_zero_confidence_without_crashing(self) -> None:
        key, confidence = estimate_key((0.0,) * 12)
        self.assertEqual(confidence, 0.0)
        self.assertIsInstance(key, str)

    def test_wrong_length_chroma_raises(self) -> None:
        with self.assertRaises(ValueError):
            estimate_key((1.0, 2.0, 3.0))


class CamelotTests(unittest.TestCase):
    def test_known_key_to_camelot_mappings(self) -> None:
        self.assertEqual(key_to_camelot("C major"), "8B")
        self.assertEqual(key_to_camelot("A minor"), "8A")
        self.assertEqual(key_to_camelot("G major"), "9B")
        self.assertEqual(key_to_camelot("E minor"), "9A")

    def test_unrecognized_key_string_returns_none(self) -> None:
        self.assertIsNone(key_to_camelot("not a key"))
        self.assertIsNone(key_to_camelot("H major"))

    def test_identical_key_is_compatible(self) -> None:
        self.assertTrue(camelot_compatible("C major", "C major"))

    def test_relative_major_minor_is_compatible(self) -> None:
        self.assertTrue(camelot_compatible("C major", "A minor"))

    def test_adjacent_number_same_letter_is_compatible(self) -> None:
        self.assertTrue(camelot_compatible("C major", "G major"))  # 8B, 9B
        self.assertTrue(camelot_compatible("C major", "F major"))  # 8B, 7B

    def test_wheel_wraps_between_1_and_12(self) -> None:
        self.assertTrue(camelot_compatible("B major", "F# major"))  # 1B, 2B... wrap check below
        self.assertTrue(camelot_compatible("E major", "B major"))  # 12B, 1B wrap

    def test_distant_keys_are_incompatible(self) -> None:
        self.assertFalse(camelot_compatible("C major", "F# major"))  # 8B vs 2B

    def test_unknown_key_is_never_compatible(self) -> None:
        self.assertFalse(camelot_compatible("C major", "not a key"))


if __name__ == "__main__":
    unittest.main()
