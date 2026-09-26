"""Inspector section for SourceType.LYRICS.

``edit`` decides which fields a lyrics source shows (split out of
SourceInspector._update_legacy_source_specific_fields). ``LyricsSection``
owns the lyrics-only fields themselves -- the ``Source.subtitle_*``
properties -- end to end (see FieldSection).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox, QWidget

from app.inspector.editors.base import FieldSection, editing, show_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector


class LyricsSection(FieldSection):
    """The lyrics-only Inspector fields (``Source.subtitle_*``)."""

    FAMILY = ("가사", "lyrics")

    ROWS = (
        ("subtitle_animation", "sub_transition"),
        ("subtitle_animation_duration", "sub_transition"),
        ("subtitle_context_lines", "sub_layout"),
        ("subtitle_next_lines", "sub_layout"),
        ("subtitle_line_spacing", "sub_layout"),
        ("subtitle_previous_opacity", "sub_prev"),
        ("subtitle_previous_blur", "sub_prev"),
        ("subtitle_timing_offset", None),
    )

    LABELS = {
        "subtitle_animation": ("가사 전환", "Lyrics transition"),
        "subtitle_animation_duration": ("전환 시간", "Transition duration"),
        "subtitle_context_lines": ("이전 가사 줄", "Previous lyric lines"),
        "subtitle_next_lines": ("다음 가사 줄", "Next lyric lines"),
        "subtitle_line_spacing": ("가사 줄 간격", "Lyric line spacing"),
        "subtitle_previous_opacity": ("이전 가사 투명도", "Previous lyric opacity"),
        "subtitle_previous_blur": ("이전 가사 블러", "Previous lyric blur"),
        "subtitle_timing_offset": ("가사 시간 보정 (초)", "Lyric timing offset (s)"),
    }

    SECTION_TITLES = {
        "sub_transition": ("전환", "Transition"),
        "sub_layout": ("줄 배치", "Line layout"),
        "sub_prev": ("이전 줄", "Previous lines"),
    }

    HELP = {
        "animation": ("현재 가사 줄이 바뀔 때 사용할 전환 효과입니다.", "Transition used when the active lyric line changes."),
        "animation_duration": ("가사 줄 전환 효과가 재생되는 시간입니다.", "Duration of the lyric-line transition."),
        "context_lines": ("현재 줄 위에 함께 표시할 이전 가사 줄 수입니다.", "Number of previous lyric lines shown above the current line."),
        "next_lines": ("현재 줄 아래에 미리 표시할 다음 가사 줄 수입니다.", "Number of upcoming lyric lines shown below the current line."),
        "line_spacing": ("가사 줄과 줄 사이의 세로 간격입니다.", "Vertical spacing between lyric lines."),
        "previous_opacity": ("지나간 가사 줄을 얼마나 흐리게 표시할지 정합니다.", "Controls how faint previous lyric lines appear."),
        "previous_blur": ("지나간 가사 줄에 적용할 흐림 정도입니다.", "Blur applied to previous lyric lines."),
        "timing_offset": ("모든 곡의 가사를 초 단위로 앞당기거나 늦추는 공통 보정입니다. 곡별 보정값과 합산됩니다.", "Global timing adjustment for lyrics on every track. It is added to each track's individual offset."),
    }

    def __init__(self, spin: Callable[[float, float, float], QDoubleSpinBox]) -> None:
        animation = QComboBox()
        for label, value in (("Glow", "glow"), ("Rise", "rise"), ("None", "none")):
            animation.addItem(label, value)
        animation_duration = spin(0.05, 1.5, 0.05)
        context_lines = QSpinBox()
        context_lines.setRange(-1, 6)  # -1 = automatic
        next_lines = QSpinBox()
        next_lines.setRange(-1, 6)
        self.widgets: dict[str, QWidget] = {
            "subtitle_animation": animation,
            "subtitle_animation_duration": animation_duration,
            "subtitle_context_lines": context_lines,
            "subtitle_next_lines": next_lines,
            "subtitle_line_spacing": spin(0, 120, 1),
            "subtitle_previous_opacity": spin(0.05, 0.9, 0.05),
            "subtitle_previous_blur": spin(0, 8, 0.5),
            "subtitle_timing_offset": spin(-5.0, 5.0, 0.01),
        }

    def hidden_when_off(self, source: Source) -> dict[str, bool]:
        """Previous-line styling only matters while previous lines are shown."""
        shows_previous = source.subtitle_context_lines != 0
        return {"subtitle_previous_opacity": shows_previous, "subtitle_previous_blur": shows_previous}

    def retranslate(self, korean: bool) -> None:
        automatic = "자동" if korean else "Auto"
        self.widgets["subtitle_context_lines"].setSpecialValueText(automatic)
        self.widgets["subtitle_next_lines"].setSpecialValueText(automatic)
        animation = self.widgets["subtitle_animation"]
        for index, label in enumerate(("글로우", "라이즈", "없음") if korean else ("Glow", "Rise", "None")):
            animation.setItemText(index, label)

    def notes(self, korean: bool) -> dict[str, str]:
        automatic = (
            "자동을 선택하면 요소 높이, 글자 크기와 줄 간격에 맞춰 표시할 가사 수를 계산합니다."
            if korean else
            "Auto calculates the visible lyric context from the source height, font size, and line spacing."
        )
        return {
            "subtitle_animation": (
                "글로우: 흐릿하게 시작해 제자리에서 또렷해지는 부드러운 전환. "
                "라이즈: 흐림·확대 없이 아래에서 위로 빠르게 미끄러져 올라오는 선명한 전환."
                if korean else
                "Glow: a soft transition that starts blurred and sharpens in place. "
                "Rise: a crisp upward slide from below, with no blur or scale."
            ),
            "subtitle_context_lines": automatic,
            "subtitle_next_lines": automatic,
        }


_OWN_FIELD_KEYS = (
    "text", "font_size", "font_weight", "font_family",
    "text_stroke_color", "text_stroke_width", "text_alignment",
    *(key for key, _section in LyricsSection.ROWS),
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    with editing(inspector, source):
        show_fields(inspector, _OWN_FIELD_KEYS)
