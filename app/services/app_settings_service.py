"""Persistent application and default render settings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from app.renderer.ffmpeg_renderer import RenderSettings


RESOLUTIONS: dict[str, tuple[int, int]] = {
    "1280 × 720 (HD)": (1280, 720),
    "1920 × 1080 (Full HD)": (1920, 1080),
    "2560 × 1440 (QHD)": (2560, 1440),
    "3840 × 2160 (4K)": (3840, 2160),
}
VIDEO_ENCODERS: dict[str, str] = {
    "CPU · H.264 (libx264)": "libx264",
    "CPU · H.265 (libx265)": "libx265",
    "NVIDIA GPU · H.264 (NVENC)": "h264_nvenc",
    "NVIDIA GPU · H.265 (NVENC)": "hevc_nvenc",
    "Intel GPU · H.264 (Quick Sync)": "h264_qsv",
    "Intel GPU · H.265 (Quick Sync)": "hevc_qsv",
    "AMD GPU · H.264 (AMF)": "h264_amf",
    "AMD GPU · H.265 (AMF)": "hevc_amf",
}
VIDEO_CODECS = tuple(VIDEO_ENCODERS.values())
ENCODING_PRESETS = (
    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
    "slow", "slower", "veryslow",
)
AUDIO_BITRATES = ("128k", "192k", "256k", "320k")


@dataclass(frozen=True, slots=True)
class AppSettings:
    """User-configurable defaults used by each new render."""

    ffmpeg_path: str = ""
    output_directory: str = ""
    resolution_name: str = "1920 × 1080 (Full HD)"
    fps: int = 30
    video_codec: str = "libx264"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"
    smooth_scrolling: bool = True
    smooth_scroll_duration_ms: int = 180

    @property
    def resolution(self) -> tuple[int, int]:
        """Return the validated pixel dimensions for the selected resolution."""
        return RESOLUTIONS.get(self.resolution_name, RESOLUTIONS["1920 × 1080 (Full HD)"])

    def render_settings(self) -> RenderSettings:
        """Convert persisted defaults into settings accepted by the FFmpeg renderer."""
        width, height = self.resolution
        return RenderSettings(
            fps=self.fps,
            video_codec=self.video_codec,
            crf=self.crf,
            preset=self.preset,
            audio_bitrate=self.audio_bitrate,
            output_width=width,
            output_height=height,
        )


class AppSettingsService(QObject):
    """Loads and writes settings independently from the main window."""

    changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings()
        self._current = self._load()

    @property
    def current(self) -> AppSettings:
        """Return the currently active immutable settings snapshot."""
        return self._current

    def save(self, settings: AppSettings) -> None:
        """Persist validated settings and notify dependent UI components."""
        normalized = AppSettings(
            ffmpeg_path=settings.ffmpeg_path.strip(),
            output_directory=settings.output_directory.strip(),
            resolution_name=(settings.resolution_name if settings.resolution_name in RESOLUTIONS
                             else "1920 × 1080 (Full HD)"),
            fps=settings.fps if settings.fps in {24, 25, 30, 50, 60} else 30,
            video_codec=settings.video_codec if settings.video_codec in VIDEO_CODECS else "libx264",
            crf=max(0, min(51, settings.crf)),
            preset=settings.preset if settings.preset in ENCODING_PRESETS else "medium",
            audio_bitrate=(settings.audio_bitrate if settings.audio_bitrate in AUDIO_BITRATES
                           else "192k"),
            smooth_scrolling=bool(settings.smooth_scrolling),
            smooth_scroll_duration_ms=max(
                80, min(420, int(settings.smooth_scroll_duration_ms))
            ),
        )
        self._settings.beginGroup("export")
        self._settings.setValue("ffmpeg_path", normalized.ffmpeg_path)
        self._settings.setValue("output_directory", normalized.output_directory)
        self._settings.setValue("resolution", normalized.resolution_name)
        self._settings.setValue("fps", normalized.fps)
        self._settings.setValue("video_codec", normalized.video_codec)
        self._settings.setValue("crf", normalized.crf)
        self._settings.setValue("preset", normalized.preset)
        self._settings.setValue("audio_bitrate", normalized.audio_bitrate)
        self._settings.endGroup()
        self._settings.beginGroup("interface")
        self._settings.setValue("smooth_scrolling", normalized.smooth_scrolling)
        self._settings.setValue(
            "smooth_scroll_duration_ms", normalized.smooth_scroll_duration_ms
        )
        self._settings.endGroup()
        self._current = normalized
        self.changed.emit(normalized)

    def _load(self) -> AppSettings:
        self._settings.beginGroup("export")
        values = AppSettings(
            ffmpeg_path=str(self._settings.value("ffmpeg_path", "")),
            output_directory=str(self._settings.value("output_directory", "")),
            resolution_name=str(self._settings.value("resolution", "1920 × 1080 (Full HD)")),
            fps=self._int_value("fps", 30),
            video_codec=str(self._settings.value("video_codec", "libx264")),
            crf=self._int_value("crf", 18),
            preset=str(self._settings.value("preset", "medium")),
            audio_bitrate=str(self._settings.value("audio_bitrate", "192k")),
        )
        self._settings.endGroup()
        self._settings.beginGroup("interface")
        values = replace(
            values,
            smooth_scrolling=self._bool_value("smooth_scrolling", True),
            smooth_scroll_duration_ms=max(
                80, min(420, self._int_value("smooth_scroll_duration_ms", 180))
            ),
        )
        self._settings.endGroup()
        return values

    def _int_value(self, key: str, default: int) -> int:
        try:
            return int(self._settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def _bool_value(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def default_output_directory() -> Path:
        """Return a writable default without creating any folder prematurely."""
        return Path.home() / "Videos" / "Playlist Canvas"
