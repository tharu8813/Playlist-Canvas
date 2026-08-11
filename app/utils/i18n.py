"""Small dependency-free runtime translation service for the application UI."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QObject, QSettings, Signal


class Language(str, Enum):
    """Languages currently available to the desktop application."""

    KOREAN = "ko"
    ENGLISH = "en"


_TEXT: dict[str, dict[Language, str]] = {
    "app_title": {Language.KOREAN: "Playlist Canvas — 새 프로젝트", Language.ENGLISH: "Playlist Canvas — Untitled Project"},
    "new": {Language.KOREAN: "새로 만들기", Language.ENGLISH: "New"},
    "open": {Language.KOREAN: "열기", Language.ENGLISH: "Open"},
    "save": {Language.KOREAN: "저장", Language.ENGLISH: "Save"},
    "fit_canvas": {Language.KOREAN: "캔버스 맞춤", Language.ENGLISH: "Fit canvas"},
    "grid": {Language.KOREAN: "그리드", Language.ENGLISH: "Grid"},
    "snap": {Language.KOREAN: "스냅", Language.ENGLISH: "Snap"},
    "delete": {Language.KOREAN: "삭제", Language.ENGLISH: "Delete"},
    "export": {Language.KOREAN: "내보내기", Language.ENGLISH: "Export"},
    "language": {Language.KOREAN: "언어", Language.ENGLISH: "Language"},
    "add_to_canvas": {Language.KOREAN: "캔버스에 추가", Language.ENGLISH: "Add to canvas"},
    "image": {Language.KOREAN: "이미지", Language.ENGLISH: "Image"},
    "text": {Language.KOREAN: "텍스트", Language.ENGLISH: "Text"},
    "shape": {Language.KOREAN: "도형", Language.ENGLISH: "Shape"},
    "progress_bar": {Language.KOREAN: "진행 바", Language.ENGLISH: "Progress bar"},
    "album_cover": {Language.KOREAN: "앨범 커버", Language.ENGLISH: "Album cover"},
    "time": {Language.KOREAN: "시간", Language.ENGLISH: "Time"},
    "logo": {Language.KOREAN: "로고", Language.ENGLISH: "Logo"},
    "watermark": {Language.KOREAN: "워터마크", Language.ENGLISH: "Watermark"},
    "background": {Language.KOREAN: "배경", Language.ENGLISH: "Background"},
    "audio_visualizer": {Language.KOREAN: "오디오 비주얼라이저", Language.ENGLISH: "Audio visualizer"},
    "playlist": {Language.KOREAN: "플레이리스트", Language.ENGLISH: "Playlist"},
    "timeline": {Language.KOREAN: "타임라인", Language.ENGLISH: "Timeline"},
    "inspector": {Language.KOREAN: "속성", Language.ENGLISH: "Inspector"},
    "select_object": {Language.KOREAN: "요소를 선택해 속성을 편집하세요.", Language.ENGLISH: "Select an element to edit its properties."},
    "content": {Language.KOREAN: "콘텐츠", Language.ENGLISH: "Content"},
    "transform": {Language.KOREAN: "변형", Language.ENGLISH: "Transform"},
    "appearance": {Language.KOREAN: "모양", Language.ENGLISH: "Appearance"},
    "track_coming": {Language.KOREAN: "트랙 편집기는 Phase 1C에서 추가됩니다", Language.ENGLISH: "Track editor is added in Phase 1C"},
    "timeline_coming": {Language.KOREAN: "타임라인 편집은 Phase 3에서 연결됩니다", Language.ENGLISH: "Timeline editing will be connected in Phase 3"},
}


class Translator(QObject):
    """Publishes language changes and persists the selected locale."""

    language_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        saved = QSettings().value("language", Language.KOREAN.value)
        self._language = Language(saved) if saved in {item.value for item in Language} else Language.KOREAN

    @property
    def language(self) -> Language:
        """Return the active application language."""
        return self._language

    def set_language(self, language: Language) -> None:
        """Switch language and store the user's selection."""
        if language is self._language:
            return
        self._language = language
        QSettings().setValue("language", language.value)
        self.language_changed.emit()

    def text(self, key: str) -> str:
        """Return a translated UI string by key."""
        return _TEXT[key][self._language]
