"""Configurable, deployment-safe AI Project Builder prompt dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from app.services.ai_project_prompt_service import (
    AIProjectPromptService, AIProjectPromptSettings,
)
from app.utils.i18n import Translator


class AIProjectBuilderDialog(QDialog):
    """Edit prompt behavior, embed a brief, preview, and copy the result."""

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.prompt_service = AIProjectPromptService(self)
        self._updating = False
        self.setMinimumSize(900, 650)
        self.resize(1120, 780)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        self.heading = QLabel()
        self.heading.setObjectName("panelTitle")
        self.description = QLabel()
        self.description.setObjectName("mutedLabel")
        self.description.setWordWrap(True)
        root.addWidget(self.heading)
        root.addWidget(self.description)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_settings_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([440, 650])
        root.addWidget(splitter, 1)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

        self._connect_settings()
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self._load_settings(self.prompt_service.current)
        self._refresh_prompt()

    def _build_settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)

        self.brief_group = QGroupBox()
        brief_layout = QVBoxLayout(self.brief_group)
        self.brief_help = QLabel()
        self.brief_help.setObjectName("mutedLabel")
        self.brief_help.setWordWrap(True)
        self.project_brief_edit = QPlainTextEdit()
        self.project_brief_edit.setObjectName("projectBriefEdit")
        self.project_brief_edit.setMinimumHeight(120)
        brief_layout.addWidget(self.brief_help)
        brief_layout.addWidget(self.project_brief_edit)
        layout.addWidget(self.brief_group)

        self.workflow_group = QGroupBox()
        workflow_form = QFormLayout(self.workflow_group)
        self.language_combo = QComboBox()
        self.question_policy_combo = QComboBox()
        self.assumption_policy_combo = QComboBox()
        self.detail_combo = QComboBox()
        self.workflow_labels: dict[str, QLabel] = {}
        for key, widget in (
            ("language", self.language_combo),
            ("questions", self.question_policy_combo),
            ("assumptions", self.assumption_policy_combo),
            ("detail", self.detail_combo),
        ):
            label = QLabel()
            self.workflow_labels[key] = label
            workflow_form.addRow(label, widget)
        layout.addWidget(self.workflow_group)

        self.output_group = QGroupBox()
        output_form = QFormLayout(self.output_group)
        self.format_combo = QComboBox()
        self.deliverable_combo = QComboBox()
        self.content_policy_combo = QComboBox()
        self.missing_media_combo = QComboBox()
        self.output_labels: dict[str, QLabel] = {}
        for key, widget in (
            ("format", self.format_combo),
            ("deliverable", self.deliverable_combo),
            ("content", self.content_policy_combo),
            ("missing", self.missing_media_combo),
        ):
            label = QLabel()
            self.output_labels[key] = label
            output_form.addRow(label, widget)
        layout.addWidget(self.output_group)

        self.design_group = QGroupBox()
        design_form = QFormLayout(self.design_group)
        self.canvas_combo = QComboBox()
        self.style_combo = QComboBox()
        self.feature_policy_combo = QComboBox()
        self.design_labels: dict[str, QLabel] = {}
        for key, widget in (
            ("canvas", self.canvas_combo),
            ("style", self.style_combo),
            ("features", self.feature_policy_combo),
        ):
            label = QLabel()
            self.design_labels[key] = label
            design_form.addRow(label, widget)
        self.lyrics_check = QCheckBox()
        self.audio_reactive_check = QCheckBox()
        self.motion_check = QCheckBox()
        design_form.addRow("", self.lyrics_check)
        design_form.addRow("", self.audio_reactive_check)
        design_form.addRow("", self.motion_check)
        layout.addWidget(self.design_group)

        self.reliability_group = QGroupBox()
        reliability_layout = QVBoxLayout(self.reliability_group)
        self.thumbnail_check = QCheckBox()
        self.validation_check = QCheckBox()
        self.technical_context_check = QCheckBox()
        reliability_layout.addWidget(self.thumbnail_check)
        reliability_layout.addWidget(self.validation_check)
        reliability_layout.addWidget(self.technical_context_check)
        layout.addWidget(self.reliability_group)

        self.custom_group = QGroupBox()
        custom_layout = QVBoxLayout(self.custom_group)
        self.custom_help = QLabel()
        self.custom_help.setObjectName("mutedLabel")
        self.custom_help.setWordWrap(True)
        self.custom_instructions_edit = QPlainTextEdit()
        self.custom_instructions_edit.setMinimumHeight(90)
        custom_layout.addWidget(self.custom_help)
        custom_layout.addWidget(self.custom_instructions_edit)
        layout.addWidget(self.custom_group)
        layout.addStretch()
        scroll.setWidget(panel)
        return scroll

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 0, 0)
        header = QHBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setObjectName("panelTitle")
        self.refresh_button = QPushButton()
        self.copy_button = QPushButton()
        self.copy_button.setObjectName("primaryButton")
        header.addWidget(self.preview_label)
        header.addStretch()
        header.addWidget(self.refresh_button)
        header.addWidget(self.copy_button)
        layout.addLayout(header)
        self.prompt_preview = QPlainTextEdit()
        self.prompt_preview.setReadOnly(True)
        self.prompt_preview.setObjectName("aiProjectPromptPreview")
        self.prompt_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.prompt_preview, 1)
        self.copy_status = QLabel()
        self.copy_status.setObjectName("mutedLabel")
        layout.addWidget(self.copy_status)
        self.refresh_button.clicked.connect(self._refresh_prompt)
        self.copy_button.clicked.connect(self._copy_prompt)
        return panel

    def _connect_settings(self) -> None:
        for combo in (
            self.language_combo, self.question_policy_combo,
            self.assumption_policy_combo, self.detail_combo, self.format_combo,
            self.deliverable_combo, self.content_policy_combo,
            self.missing_media_combo, self.canvas_combo, self.style_combo,
            self.feature_policy_combo,
        ):
            combo.currentIndexChanged.connect(self._setting_changed)
        for checkbox in (
            self.lyrics_check, self.audio_reactive_check, self.motion_check,
            self.thumbnail_check, self.validation_check,
            self.technical_context_check,
        ):
            checkbox.toggled.connect(self._setting_changed)
        self.project_brief_edit.textChanged.connect(self._setting_changed)
        self.custom_instructions_edit.textChanged.connect(self._setting_changed)

    def retranslate(self) -> None:
        korean = self.translator.language.value == "ko"
        self.setWindowTitle("AI 프로젝트 빌더" if korean else "AI Project Builder")
        self.heading.setText(
            "AI 프로젝트 빌더 프롬프트" if korean else "AI Project Builder prompt"
        )
        self.description.setText(
            "요구사항이 충분하면 AI가 추가 질문 없이 프로젝트를 한 번에 만들도록 설정합니다. "
            "필요한 정보가 결과를 크게 바꿀 때만 질문하도록 기본 설정되어 있습니다."
            if korean else
            "Configure a prompt that generates in one pass when the brief is sufficient and asks "
            "only when missing information would materially change the result."
        )
        self.brief_group.setTitle("프로젝트 요구사항" if korean else "Project brief")
        self.brief_help.setText(
            "여기에 만들 영상의 목적, 분위기, 곡/파일, 원하는 구성 등을 적으면 복사한 프롬프트만으로 바로 생성을 시작합니다."
            if korean else
            "Describe the purpose, mood, tracks/files, and desired layout so the copied prompt can start immediately."
        )
        self.project_brief_edit.setPlaceholderText(
            "예: 1920×1080 로파이 플레이리스트 영상. 앨범 커버, 현재 곡, 진행 바와 잔잔한 비주얼라이저를 포함..."
            if korean else
            "Example: A 1920×1080 lo-fi playlist with cover art, now playing, progress, and a subtle visualizer..."
        )
        self.workflow_group.setTitle("질문과 진행 방식" if korean else "Questions and workflow")
        self.output_group.setTitle("출력과 미디어" if korean else "Output and media")
        self.design_group.setTitle("디자인 기본값" if korean else "Design defaults")
        self.reliability_group.setTitle("검증과 호환성" if korean else "Validation and compatibility")
        self.custom_group.setTitle("추가 지침" if korean else "Additional instructions")
        self.custom_help.setText(
            "금지 요소, 브랜드 규칙, 파일명 같은 고정 조건을 자유롭게 입력하세요."
            if korean else
            "Add fixed constraints such as exclusions, brand rules, or a filename."
        )
        self.workflow_labels["language"].setText("대화 언어" if korean else "Language")
        self.workflow_labels["questions"].setText("질문 정책" if korean else "Question policy")
        self.workflow_labels["assumptions"].setText("AI 판단 범위" if korean else "AI discretion")
        self.workflow_labels["detail"].setText("질문 상세도" if korean else "Question depth")
        self.output_labels["format"].setText("프로젝트 형식" if korean else "Project format")
        self.output_labels["deliverable"].setText("최종 산출물" if korean else "Deliverable")
        self.output_labels["content"].setText("콘텐츠 처리" if korean else "Content handling")
        self.output_labels["missing"].setText("누락 미디어" if korean else "Missing media")
        self.design_labels["canvas"].setText("캔버스" if korean else "Canvas")
        self.design_labels["style"].setText("스타일" if korean else "Style")
        self.design_labels["features"].setText("기능 사용" if korean else "Feature use")
        self.lyrics_check.setText("필요할 때 가사/자막 사용" if korean else "Allow lyrics/subtitles when useful")
        self.audio_reactive_check.setText("오디오 반응형 요소 사용" if korean else "Allow audio-reactive elements")
        self.motion_check.setText("애니메이션과 곡 전환 사용" if korean else "Allow animation and track transitions")
        self.thumbnail_check.setText("프로젝트 썸네일 생성" if korean else "Create a project thumbnail")
        self.validation_check.setText("생성 파일을 다시 열어 검증" if korean else "Reopen and validate the result")
        self.technical_context_check.setText("상세 호환 규격 포함" if korean else "Include detailed compatibility contract")
        self.preview_label.setText("생성된 프롬프트" if korean else "Generated prompt")
        self.refresh_button.setText("새로 고침" if korean else "Refresh")
        self.copy_button.setText("프롬프트 복사" if korean else "Copy prompt")
        self.button_box.button(QDialogButtonBox.StandardButton.Close).setText(
            "닫기" if korean else "Close"
        )
        self._translate_combos(korean)
        if not self._updating:
            self._refresh_prompt()

    def _translate_combos(self, korean: bool) -> None:
        mappings = (
            (self.language_combo, (("앱 언어 사용", "auto"), ("한국어", "ko"), ("영어", "en")) if korean else (("Use app language", "auto"), ("Korean", "ko"), ("English", "en"))),
            (self.question_policy_combo, (("필요할 때만 질문 (권장)", "adaptive"), ("질문 없이 바로 생성", "never"), ("항상 먼저 질문", "always")) if korean else (("Ask only when needed (Recommended)", "adaptive"), ("Never ask; generate now", "never"), ("Always ask first", "always"))),
            (self.assumption_policy_combo, (("보수적", "conservative"), ("균형", "balanced"), ("창의적", "creative")) if korean else (("Conservative", "conservative"), ("Balanced", "balanced"), ("Creative", "creative"))),
            (self.detail_combo, (("간단", "quick"), ("표준", "standard"), ("상세", "detailed")) if korean else (("Quick", "quick"), ("Standard", "standard"), ("Detailed", "detailed"))),
            (self.format_combo, (("단일 패키지 (.pvsproj)", "pvsproj"), ("프로젝트 JSON", "json")) if korean else (("Portable package (.pvsproj)", "pvsproj"), ("Project JSON", "json"))),
            (self.deliverable_combo, (("프로젝트 파일", "project"), ("제작 프롬프트", "blueprint"), ("프로젝트 + 제작 프롬프트", "project_and_blueprint")) if korean else (("Project file", "project"), ("Production prompt", "blueprint"), ("Project + production prompt", "project_and_blueprint"))),
            (self.content_policy_combo, (("AI가 자동 판단", "auto"), ("필요할 때 질문", "ask"), ("프로젝트에 포함", "embed"), ("외부 경로 참조", "reference")) if korean else (("Let AI decide", "auto"), ("Ask when needed", "ask"), ("Embed in project", "embed"), ("Reference external paths", "reference"))),
            (self.missing_media_combo, (("플레이스홀더로 대체", "placeholder"), ("선택 요소 생략", "omit"), ("필수 파일만 질문", "ask")) if korean else (("Use placeholders", "placeholder"), ("Omit optional elements", "omit"), ("Ask for required files", "ask"))),
            (self.canvas_combo, (("요청에 맞게 자동", "auto"), ("가로 1920×1080", "landscape"), ("세로 1080×1920", "portrait"), ("정사각 1080×1080", "square")) if korean else (("Infer from request", "auto"), ("Landscape 1920×1080", "landscape"), ("Portrait 1080×1920", "portrait"), ("Square 1080×1080", "square"))),
            (self.style_combo, (("장르에 맞게 자동", "auto"), ("미니멀", "minimal"), ("모던", "modern"), ("시네마틱", "cinematic"), ("에너지틱", "energetic")) if korean else (("Infer from genre", "auto"), ("Minimal", "minimal"), ("Modern", "modern"), ("Cinematic", "cinematic"), ("Energetic", "energetic"))),
            (self.feature_policy_combo, (("요청한 기능만", "request_only"), ("필요한 기능 자동 선택", "smart"), ("표현 기능 적극 활용", "showcase")) if korean else (("Requested features only", "request_only"), ("Select useful features", "smart"), ("Showcase features", "showcase"))),
        )
        self._updating = True
        try:
            for combo, entries in mappings:
                current = combo.currentData()
                combo.clear()
                for label, value in entries:
                    combo.addItem(label, value)
                index = combo.findData(current)
                combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._updating = False

    def _load_settings(self, settings: AIProjectPromptSettings) -> None:
        self._updating = True
        try:
            for combo, value in (
                (self.language_combo, settings.language),
                (self.question_policy_combo, settings.question_policy),
                (self.assumption_policy_combo, settings.assumption_policy),
                (self.detail_combo, settings.detail_level),
                (self.format_combo, settings.output_format),
                (self.deliverable_combo, settings.deliverable),
                (self.content_policy_combo, settings.content_policy),
                (self.missing_media_combo, settings.missing_media_policy),
                (self.canvas_combo, settings.canvas_preset),
                (self.style_combo, settings.design_style),
                (self.feature_policy_combo, settings.feature_policy),
            ):
                index = combo.findData(value)
                combo.setCurrentIndex(index if index >= 0 else 0)
            self.lyrics_check.setChecked(settings.include_lyrics)
            self.audio_reactive_check.setChecked(settings.include_audio_reactive)
            self.motion_check.setChecked(settings.include_motion)
            self.thumbnail_check.setChecked(settings.include_thumbnail)
            self.validation_check.setChecked(settings.validate_output)
            self.technical_context_check.setChecked(settings.include_technical_context)
            self.project_brief_edit.setPlainText(settings.project_brief)
            self.custom_instructions_edit.setPlainText(settings.custom_instructions)
        finally:
            self._updating = False

    def _settings(self) -> AIProjectPromptSettings:
        return AIProjectPromptSettings(
            language=str(self.language_combo.currentData() or "auto"),
            question_policy=str(self.question_policy_combo.currentData() or "adaptive"),
            assumption_policy=str(self.assumption_policy_combo.currentData() or "balanced"),
            detail_level=str(self.detail_combo.currentData() or "standard"),
            output_format=str(self.format_combo.currentData() or "pvsproj"),
            deliverable=str(self.deliverable_combo.currentData() or "project"),
            content_policy=str(self.content_policy_combo.currentData() or "auto"),
            missing_media_policy=str(self.missing_media_combo.currentData() or "placeholder"),
            canvas_preset=str(self.canvas_combo.currentData() or "auto"),
            design_style=str(self.style_combo.currentData() or "auto"),
            feature_policy=str(self.feature_policy_combo.currentData() or "smart"),
            include_lyrics=self.lyrics_check.isChecked(),
            include_audio_reactive=self.audio_reactive_check.isChecked(),
            include_motion=self.motion_check.isChecked(),
            include_thumbnail=self.thumbnail_check.isChecked(),
            validate_output=self.validation_check.isChecked(),
            include_technical_context=self.technical_context_check.isChecked(),
            project_brief=self.project_brief_edit.toPlainText(),
            custom_instructions=self.custom_instructions_edit.toPlainText(),
        )

    def _setting_changed(self, _value: object = None) -> None:
        if self._updating:
            return
        settings = self._settings()
        self.prompt_service.save(settings)
        self._refresh_prompt()
        self.copy_status.clear()

    def _refresh_prompt(self) -> None:
        self.prompt_preview.setPlainText(
            self.prompt_service.generate(self._settings(), self.translator.language.value)
        )

    def _copy_prompt(self) -> None:
        prompt = self.prompt_preview.toPlainText().strip()
        if not prompt:
            return
        QApplication.clipboard().setText(prompt)
        self.copy_status.setText(
            "클립보드에 복사했습니다. 다른 AI에게 그대로 붙여 넣으세요."
            if self.translator.language.value == "ko" else
            "Copied to the clipboard. Paste it directly into another AI agent."
        )
