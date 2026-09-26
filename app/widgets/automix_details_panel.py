"""Preview's collapsible "AutoMix details": why each transition sounds the way it does.

Read-only: it shows ``app.automix.diagnostics.transition_rows`` of the plan
Preview is playing and never analyzes, plans or renders anything itself.
"""

from __future__ import annotations

import math

from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from app.automix.diagnostics import rows_to_json, transition_rows
from app.utils.i18n import Language, Translator

_STYLE_LABELS = {
    "bass_swap": ("베이스 스왑", "Bass swap"),
    "vocal_safe_eq": ("보컬 보호 EQ", "Vocal-safe EQ"),
    "filter_blend": ("필터 블렌드", "Filter blend"),
    "short_fade": ("짧은 페이드", "Short fade"),
    "filter_sweep": ("필터 스윕", "Filter sweep"),
    "drop_in": ("여운 위로 시작", "Drop in over the tail"),
    "legacy": ("기본 크로스페이드", "Plain crossfade"),
    "sequential": ("이어서 재생", "Back to back"),
    "gap": ("간격", "Gap"),
}
_DETAIL_FIELDS = (
    # (row key, Korean label, English label)
    ("strategy", "전략", "Strategy"),
    ("bars", "마디", "Bars"),
    ("score", "후보 점수", "Candidate score"),
    ("outgoing_bpm", "나가는 곡 BPM", "Outgoing BPM"),
    ("incoming_bpm", "들어오는 곡 BPM", "Incoming BPM"),
    ("target_bpm", "목표 BPM", "Target BPM"),
    ("outgoing_rate", "나가는 곡 속도(전환 중)", "Outgoing rate (in overlap)"),
    ("tempo_ramp_seconds", "템포 맞춤 구간(초)", "Tempo ramp (s)"),
    ("tempo_delta_percent", "템포 차이 %", "Tempo delta %"),
    ("outgoing_cue", "나가는 곡 큐(초)", "Outgoing cue (s)"),
    ("incoming_cue", "들어오는 곡 큐(초)", "Incoming cue (s)"),
    ("outgoing_vocal_outro_start", "나가는 곡 마지막 보컬 끝(초)", "Outgoing last vocal ends (s)"),
    ("incoming_vocal_intro_end", "들어오는 곡 첫 보컬 시작(초)", "Incoming first vocal starts (s)"),
    ("outgoing_structure_anchor", "구조 큐: 아웃트로", "Structure cue: outro"),
    ("incoming_structure_anchor", "구조 큐: 인트로 끝", "Structure cue: intro end"),
    ("energy_delta", "에너지 차이", "Energy delta"),
    ("vocal_overlap", "보컬 겹침", "Vocal overlap"),
    ("key_clash", "키 충돌", "Key clash"),
    ("outgoing_downbeat_confidence", "나가는 곡 다운비트 신뢰도", "Outgoing downbeat confidence"),
    ("incoming_downbeat_confidence", "들어오는 곡 다운비트 신뢰도", "Incoming downbeat confidence"),
    ("outgoing_analyzer", "나가는 곡 분석기", "Outgoing analyzer"),
    ("incoming_analyzer", "들어오는 곡 분석기", "Incoming analyzer"),
)


def _value_text(value: object, korean: bool) -> str:
    if value is None:
        return "알 수 없음" if korean else "unknown"
    if isinstance(value, bool):
        return ("예" if value else "아니요") if korean else ("yes" if value else "no")
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60:d}:{whole % 60:02d}"


class AutoMixDetailsPanel(QFrame):
    """Transitions of the playing plan, the one under the playhead marked."""

    def __init__(
        self, translator: Translator, parent: QWidget | None = None, *, automix: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("automixDetailsPanel")
        self.translator = translator
        # Only AutoMix plans can fall back; in crossfade mode "legacy" is the plan itself.
        self.automix = automix
        self.rows: list[dict[str, object]] = []
        self._windows: list[tuple[float, float]] = []
        self._current = -1
        self._state = ("waiting", None)
        self._progress_message = ""
        self._failed = False
        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_button.toggled.connect(self._set_expanded)
        self.status_label = QLabel()
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        self.body = QWidget()
        self.list = QListWidget()
        self.list.setObjectName("automixTransitionList")
        self.list.setMaximumHeight(140)
        self.list.currentRowChanged.connect(lambda _row: self._show_details())
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(170)
        self.copy_text_button = QPushButton()
        self.copy_json_button = QPushButton()
        self.copy_text_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.as_text()))
        self.copy_json_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(rows_to_json(self.rows)))
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)
        body_layout.addWidget(self.list)
        body_layout.addWidget(self.details)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_text_button)
        buttons.addWidget(self.copy_json_button)
        body_layout.addLayout(buttons)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.body)
        self.body.setVisible(False)
        self.retranslate()

    # -- state ---------------------------------------------------------------

    def set_plan(self, plan, tracks, *, state: str, ready_through: int | None = None) -> None:
        """``state``: "waiting" (no mix yet), "provisional" (partial mix) or "final"."""
        titles = {track.id: track.title or track.id for track in tracks}
        self.rows = transition_rows(plan, titles)
        self._windows = [
            (float(row["timeline_start"]), float(row["timeline_start"]) + float(row["duration"]))
            for row in self.rows
        ]
        self._state = (state, ready_through)
        selected = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for row in self.rows:
            self.list.addItem(QListWidgetItem(self._row_title(row)))
        self.list.blockSignals(False)
        self._current = -1
        if self.rows:
            self.list.setCurrentRow(min(max(selected, 0), len(self.rows) - 1))
        self._show_details()
        self._update_status()

    def set_progress_message(self, message: str) -> None:
        """Live preparation text ("Analyzing 4 / 12 ..."), shown until a mix plays."""
        self._progress_message = message or ""
        self._update_status()

    def set_failed(self) -> None:
        """The mix could not be prepared: Preview keeps playing what it already has."""
        self._failed = True
        self._update_status()

    def set_playhead(self, seconds: float) -> None:
        """Mark the transition sounding at ``seconds`` (cheap: called every frame)."""
        current = next((index for index, (start, end) in enumerate(self._windows)
                        if start <= seconds < end), -1)
        if current == self._current:
            return
        for index, bold in ((self._current, False), (current, True)):
            item = self.list.item(index) if index >= 0 else None
            if item is not None:
                font = QFont(item.font())
                font.setBold(bold)
                item.setFont(font)
        self._current = current

    @property
    def current_index(self) -> int:
        return self._current

    def as_text(self) -> str:
        korean = self._korean()
        blocks = []
        for index in range(len(self.rows)):
            blocks.append(self._detail_text(index, korean))
        return "\n\n".join(blocks)

    # -- presentation ----------------------------------------------------------

    def _korean(self) -> bool:
        return self.translator.language is Language.KOREAN

    def _style(self, row: dict[str, object]) -> str:
        key = str(row["dsp"] or row["type"])
        korean, english = _STYLE_LABELS.get(key, (key, key))
        return korean if self._korean() else english

    def _row_title(self, row: dict[str, object]) -> str:
        index = int(row["index"])
        duration = float(row["duration"])
        length = f" · {duration:.1f}s" if duration else ""
        return f"{index:02d}→{index + 1:02d}  {_clock(float(row['timeline_start']))}{length} · {self._style(row)}"

    def _detail_text(self, index: int, korean: bool) -> str:
        row = self.rows[index]
        lines = [f"{row['from']} → {row['to']}",
                 f"{'시작' if korean else 'Start'}: {_clock(float(row['timeline_start']))}"
                 f" · {'길이' if korean else 'Length'}: {float(row['duration']):.1f}s"
                 f" · {self._style(row)}"]
        for key, korean_label, english_label in _DETAIL_FIELDS:
            if key in row:
                lines.append(f"{korean_label if korean else english_label}: {_value_text(row[key], korean)}")
        reasons = str(row.get("reasons") or "")
        if reasons:
            lines.append("근거:" if korean else "Why:")
            lines.extend(f"  {reason}" for reason in reasons.split("; "))
        elif row["duration"]:
            lines.append("선택 근거 정보가 없는 전환입니다." if korean else "No selection reasons recorded for this transition.")
        elif self.automix and row["type"] == "sequential":
            lines.append(self._back_to_back_reason(korean))
        return "\n".join(lines)

    @staticmethod
    def _back_to_back_reason(korean: bool) -> str:
        return ("분석하지 못했거나 섞을 길이가 부족한 곡은 섞지 않고, "
                "앞 곡의 마지막 소리 바로 뒤에 다음 곡을 이어 재생합니다." if korean
                else "A track without analysis, or too short to blend, is not mixed: "
                     "the next track starts right after its last sound.")

    def _show_details(self) -> None:
        row = self.list.currentRow()
        if not self.rows:
            self.details.setPlainText(
                "이 미리보기에는 곡 사이 전환이 없습니다." if self._korean()
                else "This preview has no transitions between tracks."
            )
        elif 0 <= row < len(self.rows):
            self.details.setPlainText(self._detail_text(row, self._korean()))

    def _update_status(self) -> None:
        """One status line, symbol + text (never color alone): ● preparing, ◐ partial, ✓ final, ! fallback."""
        korean = self._korean()
        state, ready_through = self._state
        mixed = sum(1 for row in self.rows if float(row["duration"]) > 0.0)
        plain = sum(1 for row in self.rows if row["dsp"] == "legacy") if self.automix else 0
        # Planned back-to-back junctions (a provisional plan's unanalyzed tail is not planned yet).
        planned = len(self.rows) if state == "final" else (ready_through or 1) - 1
        joined = sum(1 for row in self.rows if row["type"] == "sequential" and int(row["index"]) <= planned
                     ) if self.automix else 0
        if self._failed and state != "final":
            if state == "provisional":
                text = (f"! {ready_through}번째 곡까지만 AutoMix 적용 · 이후는 곡을 차례로 재생합니다" if korean
                        else f"! AutoMix only through track {ready_through} · then tracks play back to back")
            else:
                text = ("! 믹스를 준비하지 못해 곡을 차례로 재생합니다" if korean
                        else "! Could not prepare the mix · playing tracks back to back")
        elif state == "provisional":
            text = (f"◐ 임시 계획 · {ready_through}번째 곡까지 AutoMix 적용됨 · 전환 {mixed}개" if korean
                    else f"◐ Provisional · AutoMix through track {ready_through} · {mixed} transition(s)")
        elif state == "final":
            text = f"✓ 최종 계획 · 전환 {mixed}개" if korean else f"✓ Final plan · {mixed} transition(s)"
        elif self._progress_message:
            text = f"● {self._progress_message}"
        else:
            text = ("● 믹스 준비 중 · 곡을 차례로 재생합니다" if korean
                    else "● Preparing the mix · playing tracks back to back")
        if plain and state in {"provisional", "final"}:
            text += (f" · {plain}개는 기본 크로스페이드" if korean
                     else f" · {plain} as plain crossfade")
        if joined and state in {"provisional", "final"}:
            text += f" · {joined}개는 바로 이어 재생" if korean else f" · {joined} back to back"
        self.status_label.setText(text)
        explanation = " ".join(part for part in (
            ("미리보기 믹스를 만들지 못했습니다. 원인은 로그에 기록됩니다." if korean
             else "The preview mix could not be rendered; the cause is in the log.") if self._failed else "",
            ("기본 크로스페이드는 분석이 부족하거나 템포가 맞지 않는 곡 사이에 AutoMix 대신 쓰였습니다." if korean
             else "Plain crossfades replace AutoMix where analysis was incomplete or tempos did not match.")
            if plain else "",
            self._back_to_back_reason(korean) if joined else "",
            ("미리보기는 계속 재생할 수 있습니다." if korean else "Preview keeps playing.")
            if plain or self._failed else "",
        ) if part)
        self.status_label.setToolTip(f"{text}\n{explanation}".strip())
        self.status_label.setAccessibleName(("믹스 상태: " if korean else "Mix status: ") + text.lstrip("●◐✓! "))
        self.status_label.setAccessibleDescription(explanation)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def retranslate(self) -> None:
        korean = self._korean()
        self.toggle_button.setText("전환 상세" if korean else "Transition details")
        self.toggle_button.setToolTip(
            "각 전환의 위치, 방식, 선택 근거를 봅니다." if korean
            else "See where each transition sits, how it is mixed, and why."
        )
        self.copy_text_button.setText("텍스트 복사" if korean else "Copy text")
        self.copy_json_button.setText("JSON 복사" if korean else "Copy JSON")
        for index, row in enumerate(self.rows):
            self.list.item(index).setText(self._row_title(row))
        self._show_details()
        self._update_status()


def ready_through(plan, covered_until: float) -> int:
    """How many leading clips a partial mix covering ``covered_until`` seconds fully contains."""
    if math.isinf(covered_until):
        return len(plan.audio.clips)
    return sum(1 for clip in plan.audio.clips if clip.timeline_end <= covered_until + 1e-6)
