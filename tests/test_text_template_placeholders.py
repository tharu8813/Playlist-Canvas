import unittest

from app.preview.text_template import expand_placeholder_labels


class ExpandPlaceholderLabelsTests(unittest.TestCase):
    def test_known_tokens_become_parenthesised_labels(self) -> None:
        self.assertEqual(
            expand_placeholder_labels("%title% — %artist%"),
            "(Title) — (Artist)",
        )
        self.assertEqual(
            expand_placeholder_labels("%title% — %artist%", korean=True),
            "(제목) — (아티스트)",
        )

    def test_surrounding_literal_text_is_preserved(self) -> None:
        self.assertEqual(
            expand_placeholder_labels("%title%이것은 제목입니다", korean=True),
            "(제목)이것은 제목입니다",
        )
        self.assertEqual(
            expand_placeholder_labels("Now playing: %title% (%track%/%track_total%)"),
            "Now playing: (Title) ((Track #)/(Track count))",
        )

    def test_unknown_tokens_are_left_untouched(self) -> None:
        self.assertEqual(
            expand_placeholder_labels("%title% %not_a_token% 100%"),
            "(Title) %not_a_token% 100%",
        )

    def test_no_tokens_returns_input_unchanged(self) -> None:
        self.assertEqual(expand_placeholder_labels("Plain heading"), "Plain heading")


if __name__ == "__main__":
    unittest.main()
