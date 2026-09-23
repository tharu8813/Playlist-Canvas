"""Modal "preparing AutoMix/crossfade preview" progress dialog.

Shown before Preview opens whenever blended preview audio is needed, so the
normal path opens Preview already on the final CompiledRenderPlan instead of
starting on the sequential timeline and jumping once the AutoMix render
catches up. Does no rendering itself -- the caller wires an already-running
PreviewAudioController's ``progress``/``audio_ready``/``audio_failed``
signals into this dialog and drives it with ``QDialog.exec()``, which blocks
the caller while keeping Qt's event loop (and this dialog's own repaints and
button clicks) alive.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from app.utils.i18n import Language, Translator


class PreviewPreparationDialog(QDialog):
    """Blocks the caller (while staying responsive) until audio is ready or skipped."""

    def __init__(self, translator: Translator, parent: QWidget | None = None, *,
                 transition_mode: str = "automix") -> None:
        super().__init__(parent)
        self.translator = translator
        self.transition_mode = transition_mode
        self.skipped = False
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(380)

        self.title_label = QLabel()
        self.title_label.setObjectName("previewPreparationTitle")
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.stage_label = QLabel()
        self.stage_label.setObjectName("mutedLabel")
        self.stage_label.setWordWrap(True)
        self.skip_button = QPushButton()
        self.skip_button.clicked.connect(self._on_skip)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stage_label)
        layout.addSpacing(6)
        layout.addWidget(self.skip_button, 0, Qt.AlignmentFlag.AlignRight)

        self.retranslate()

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        if self.transition_mode == "crossfade":
            title = "크로스페이드 미리보기 준비 중" if korean else "Preparing Crossfade Preview"
        else:
            title = "AutoMix 미리보기 준비 중" if korean else "Preparing AutoMix Preview"
        self.setWindowTitle(title)
        self.title_label.setText(title)
        self.description_label.setText(
            "최종 전환 오디오를 준비하고 있습니다." if korean else
            "Preparing the final transition audio."
        )
        self.skip_button.setText("기다리지 않고 시작" if korean else "Start Without Waiting")

    def set_progress(self, stage: str, fraction: float, message: str) -> None:
        self.progress_bar.setValue(round(max(0.0, min(1.0, fraction)) * 100))
        self.stage_label.setText(message or stage)

    def _on_skip(self) -> None:
        self.skipped = True
        self.reject()
