"""Full-playlist, audio-aware pre-export preview dialog."""

from __future__ import annotations

import logging
from math import ceil
from pathlib import Path
import threading
from dataclasses import replace

import numpy as np

from PySide6.QtCore import QElapsedTimer, QRect, QRectF, QSettings, QSignalBlocker, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QResizeEvent, QShortcut, QSurfaceFormat
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.models.playlist import PlaylistTrack
from app.models.source import SourceType
from app.preview.canvas_snapshot import CanvasSnapshot
from app.preview.text_template import format_timestamp
from app.renderer.ffmpeg_renderer import VisualizerOverlay
from app.renderer.python_visualizer import PythonVisualizerRenderer
from app.services.source_store import SourceStore
from app.services.preview_audio_settings import preview_volume, save_preview_volume
from app.utils.i18n import Language, Translator

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
except ImportError:  # pragma: no cover - platform Qt builds can omit OpenGL widgets
    QOpenGLWidget = None


TIMELINE_SCALE = 100
LOGGER = logging.getLogger(__name__)


if QOpenGLWidget is not None:
    class GpuPreviewSurface(QOpenGLWidget):
        """OpenGL-backed presentation surface for already-rendered preview frames."""

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.image = QImage()
            self._pending_image = QImage()
            self._update_queued = False
            surface_format = QSurfaceFormat()
            surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
            surface_format.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
            surface_format.setSwapInterval(1)
            surface_format.setSamples(0)
            self.setFormat(surface_format)
            self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        def set_image(self, image: QImage) -> None:
            # Keep only the newest frame.  Rendering an already obsolete preview
            # frame is the main cause of perceived stutter at 60 FPS.
            self._pending_image = image
            if self._update_queued:
                return
            self._update_queued = True
            self.update()

        def paintGL(self) -> None:  # type: ignore[override]
            if not self._pending_image.isNull():
                self.image = self._pending_image
                self._pending_image = QImage()
            self._update_queued = False
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#111820"))
            if not self.image.isNull():
                size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                x = (self.width() - size.width()) // 2
                y = (self.height() - size.height()) // 2
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                # Draw into the OpenGL paint device at the destination size;
                # do not create a CPU-scaled temporary QImage every frame.
                painter.drawImage(QRect(x, y, size.width(), size.height()), self.image)
            painter.end()
else:
    GpuPreviewSurface = None


class CpuPreviewSurface(QWidget):
    """CPU preview surface that scales during paint instead of copying a pixmap."""

    frame_presented = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = QImage()
        self._target_key: tuple[int, int, int, int] | None = None
        self._target_rect = QRect()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def set_image(self, image: QImage) -> None:
        """Present the latest frame without constructing a scaled QPixmap copy."""
        self.image = image
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111820"))
        if not self.image.isNull():
            target_key = (self.image.width(), self.image.height(), self.width(), self.height())
            if self._target_key != target_key:
                size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                x = (self.width() - size.width()) // 2
                y = (self.height() - size.height()) // 2
                self._target_rect = QRect(x, y, size.width(), size.height())
                self._target_key = target_key
            needs_scaling = self._target_rect.size() != self.image.size()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, needs_scaling)
            painter.drawImage(self._target_rect, self.image)
        painter.end()
        self.frame_presented.emit()


class PlaylistTimeline(QSlider):
    """Global playback slider with visible track boundaries and track numbers."""

    def __init__(self, tracks: list[PlaylistTrack], parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.tracks = tracks
        self._dragging = False
        self.setMinimumHeight(42)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        total = max(0.01, self.maximum() / TIMELINE_SCALE)
        current_seconds = self.value() / TIMELINE_SCALE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cursor = 0.0
        usable_width = max(1, self.width() - 18)
        for index, track in enumerate(self.tracks, start=1):
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            end = start + track.duration_seconds
            x = 9 + round(start / total * usable_width)
            active = start <= current_seconds < end or (
                index == len(self.tracks) and current_seconds >= start
            )
            painter.setPen(QPen(QColor("#7BA8D1"), 1.2))
            painter.drawLine(x, 3, x, self.height() - 10)
            label_x = max(1, min(self.width() - 25, x + 3))
            if active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#1685D1"))
                painter.drawRoundedRect(QRect(label_x, 1, 24, 18), 8, 8)
                painter.setPen(QColor("#FFFFFF"))
            else:
                painter.setPen(QColor("#9BAFC2"))
            painter.drawText(QRect(label_x, 1, 24, 18), Qt.AlignmentFlag.AlignCenter, str(index))
            cursor = end
        painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._set_value_from_position(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self._set_value_from_position(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._set_value_from_position(event.position().x())
            self._dragging = False
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_value_from_position(self, x_position: float) -> None:
        ratio = max(0.0, min(1.0, (x_position - 9) / max(1, self.width() - 18)))
        self.setValue(round(self.minimum() + ratio * (self.maximum() - self.minimum())))


class AudioAnalysisWorker(QThread):
    """Build audio-reactive preview frames without blocking the playback UI."""

    ready = Signal(str, int, object)
    failed = Signal(str)

    def __init__(self, renderer: PythonVisualizerRenderer, track: PlaylistTrack,
                 bands: int, fps: int = 30, needs_waveform: bool = False,
                 overlays: tuple[VisualizerOverlay, ...] = (),
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.renderer = renderer
        self.track = track
        self.bands = bands
        self.fps = fps
        self.needs_waveform = needs_waveform
        self.overlays = overlays
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation before the next analysis block."""
        self.cancel_event.set()

    def run(self) -> None:
        try:
            needs_meter = any(overlay.kind == "level_meter" for overlay in self.overlays)
            stereo = (
                self.renderer._decode_stereo_audio(
                    Path(self.track.file_path), self.cancel_event,
                )
                if needs_meter else None
            )
            samples = (
                np.mean(stereo, axis=1, dtype=np.float32)
                if stereo is not None else
                self.renderer._decode_mono_audio(Path(self.track.file_path), self.cancel_event)
            )
            levels = self.renderer._analyze_levels(samples, self.fps, self.bands, self.cancel_event)
            waveform = (
                self.renderer._analyze_waveform(samples, self.fps, max(32, self.bands), self.cancel_event)
                if self.needs_waveform else np.zeros((1, max(32, self.bands)), dtype=np.float32)
            )
            processed = tuple(
                (
                    np.zeros((1, 1), dtype=np.float32)
                    if overlay.kind in {"particles", "level_meter"} else
                    self.renderer.process_level_sequence(
                        waveform if overlay.kind == "waveform" else levels,
                        overlay,
                        max(4, min(96, overlay.bar_count)),
                    )
                )
                for overlay in self.overlays
            )
            raw_meter = (
                np.column_stack((
                    self.renderer._analyze_rms(stereo[:, 0], self.fps, self.cancel_event),
                    self.renderer._analyze_rms(stereo[:, 1], self.fps, self.cancel_event),
                ))
                if stereo is not None else None
            )
            meters = tuple(
                self.renderer.process_meter_sequence(raw_meter, overlay, self.fps)
                if overlay.kind == "level_meter" and raw_meter is not None else None
                for overlay in self.overlays
            )
        except Exception as error:
            if not self.cancel_event.is_set():
                self.failed.emit(str(error))
            return
        if not self.cancel_event.is_set():
            self.ready.emit(
                self.track.id, self.fps,
                {
                    "levels": levels, "waveform": waveform,
                    "processed": processed, "meters": meters,
                },
            )


class OverlayFrameWorker(QThread):
    """Pre-render a small run of pure-QImage overlay frames off the UI thread."""

    ready = Signal(str, int, int, object)
    failed = Signal(str)

    def __init__(self, track_id: str, fps: int, generation: int, start_frame: int, frame_count: int,
                 overlays: tuple[VisualizerOverlay, ...], analysis: dict[str, np.ndarray],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.track_id = track_id
        self.fps = fps
        self.generation = generation
        self.start_frame = start_frame
        self.frame_count = frame_count
        self.overlays = overlays
        self.analysis = analysis
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        """Discard this short prefetch batch at the next frame boundary."""
        self.cancel_event.set()

    def run(self) -> None:
        try:
            rendered: list[tuple[int, tuple[QImage, ...]]] = []
            spectrum = self.analysis["levels"]
            waveform = self.analysis["waveform"]
            for frame_index in range(self.start_frame, self.start_frame + self.frame_count):
                if self.cancel_event.is_set():
                    return
                layers: list[QImage] = []
                processed = self.analysis.get("processed", ())
                meters = self.analysis.get("meters", ())
                for overlay_index, overlay in enumerate(self.overlays):
                    values = waveform if overlay.kind == "waveform" else spectrum
                    is_processed = overlay_index < len(processed)
                    if is_processed:
                        values = processed[overlay_index]
                    values_for_frame = values[min(frame_index, len(values) - 1)]
                    meter_values = None
                    peak_values = None
                    if overlay_index < len(meters) and meters[overlay_index] is not None:
                        meter_levels, meter_peaks = meters[overlay_index]
                        meter_index = min(frame_index, len(meter_levels) - 1)
                        meter_values = tuple(float(value) for value in meter_levels[meter_index])
                        peak_values = tuple(float(value) for value in meter_peaks[meter_index])
                    layers.append(PythonVisualizerRenderer.preview_image(
                        overlay.width, overlay.height, overlay, values_for_frame, frame_index,
                        processed=is_processed,
                        frame_rate=self.fps,
                        channel_values=meter_values,
                        peak_values=peak_values,
                    ))
                rendered.append((frame_index, tuple(layers)))
            if not self.cancel_event.is_set():
                self.ready.emit(self.track_id, self.fps, self.generation, rendered)
        except Exception as error:
            if not self.cancel_event.is_set():
                self.failed.emit(str(error))


class ExportPreviewDialog(QDialog):
    """Play and inspect the complete playlist using export-equivalent visuals."""

    def __init__(self, scene: CanvasScene, tracks: list[PlaylistTrack], translator: Translator,
                 overlays: list[VisualizerOverlay] | None = None,
                 ffmpeg_executable: Path | None = None,
                 parent: QWidget | None = None,
                 source_store: SourceStore | None = None) -> None:
        super().__init__(parent)
        self.scene = scene
        self.tracks = tracks
        self.translator = translator
        self.overlays = list(overlays or [])
        self.source_store = source_store
        self.visualizer_renderer = (
            PythonVisualizerRenderer(ffmpeg_executable) if ffmpeg_executable and self.overlays else None
        )
        self._image = QImage()
        self._base_image = QImage()
        self._base_track_id = ""
        self._base_elapsed = -1.0
        self._base_hidden_source_ids: frozenset[str] = frozenset()
        self._source_partition_dirty = True
        self._cached_audio_dynamic_ids: frozenset[str] = frozenset()
        self._cached_always_dynamic_ids: frozenset[str] = frozenset()
        self._cached_animated_source_ids: frozenset[str] = frozenset()
        self._cached_visible_source_ids: frozenset[str] = frozenset()
        self._dynamic_region_plans: dict[frozenset[str], tuple[tuple[frozenset[str], QRectF], ...]] = {}
        self._dynamic_region_buffers: dict[frozenset[str], QImage] = {}
        self._foreground_bands: list[tuple[float, QImage]] = []
        self._playing = False
        self._advancing_playhead = False
        self._playhead_seconds = 0.0
        self._active_track_index = -1
        self._last_media_position_ms = -1
        self._preview_levels = None
        self._preview_levels_track_id = ""
        self._last_analysis_second = -1.0
        self._track_levels: dict[str, object] = {}
        self._analysis_worker: AudioAnalysisWorker | None = None
        self._analysis_track_id = ""
        self._overlay_worker: OverlayFrameWorker | None = None
        self._overlay_frame_cache: dict[tuple[str, int], tuple[QImage, ...]] = {}
        self._last_overlay_layers: tuple[QImage, ...] = ()
        self._last_overlay_track_id = ""
        self._overlay_prefetch_count = 6
        self._overlay_generation = 0
        self._idle_overlay_analyses: dict[int, dict[str, np.ndarray]] = {}
        self._refresh_queued = False
        self._closing = False
        self._overlay_error_reported = False
        self.preview_fps = 30
        self.preview_render_scale = 0.65
        self.audio_output = QAudioOutput(self)
        saved_volume = preview_volume()
        self.audio_output.setVolume(saved_volume / 100.0)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.play_timer.setInterval(33)
        self.play_timer.timeout.connect(self._advance_playback)
        self.play_clock = QElapsedTimer()
        self._frame_stats_clock = QElapsedTimer()
        self._frame_stats_clock.start()
        self._presented_frames = 0
        self._actual_preview_fps = 0.0

        self.setMinimumSize(1040, 720)
        self.resize(1240, 820)
        # Do not restore GPU mode automatically.  A stale OpenGL context or a
        # driver reset can terminate Qt before Python's crash reporter starts.
        # The user can explicitly enable the mode after the preview is visible.
        self.gpu_preview_enabled = False
        self.preview_label = CpuPreviewSurface()
        self.preview_label.setStyleSheet("background: #111820; border-radius: 8px;")
        self.preview_label.frame_presented.connect(self._record_presented_frame)
        self.preview_stack_host = QWidget()
        self.preview_stack = QStackedLayout(self.preview_stack_host)
        self.preview_stack.setContentsMargins(0, 0, 0, 0)
        self.preview_stack.addWidget(self.preview_label)
        self.gpu_surface = None
        self.hint_label = QLabel()
        self.hint_label.setObjectName("mutedLabel")
        self.hint_label.setWordWrap(True)
        self.dialog_title_label = QLabel()
        self.dialog_title_label.setObjectName("previewDialogTitle")
        self.preview_mode_label = QLabel()
        self.preview_mode_label.setObjectName("previewStatusChip")
        self.frame_rate_label = QLabel()
        self.frame_rate_label.setObjectName("previewStatusChip")
        self.frame_rate_label.setMinimumWidth(170)
        self.frame_rate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.play_button = QPushButton()
        self.play_button.setObjectName("previewPlayButton")
        self.play_button.setCheckable(True)
        self.previous_button = QPushButton()
        self.rewind_button = QPushButton()
        self.forward_button = QPushButton("+5s")
        self.next_button = QPushButton()
        for button in (
            self.previous_button, self.rewind_button,
            self.forward_button, self.next_button,
        ):
            button.setObjectName("previewTransportButton")
        self.volume_label = QLabel()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(saved_volume)
        self.volume_slider.setFixedWidth(150)
        self.volume_value_label = QLabel(f"{saved_volume}%")
        self.volume_value_label.setObjectName("previewValueLabel")
        self.volume_value_label.setMinimumWidth(42)
        self.quality_label = QLabel()
        self.quality_combo = QComboBox()
        self.quality_combo.setMinimumWidth(145)
        for label, fps, scale in (("Low - 15 FPS", 15, 0.55), ("Normal - 30 FPS", 30, 0.65),
                                  ("High - 45 FPS", 45, 0.72), ("Very high - 60 FPS", 60, 0.72)):
            self.quality_combo.addItem(label, (fps, scale))
        saved_quality = max(0, min(3, QSettings().value("preview_quality_index", 1, int)))
        self.quality_combo.setCurrentIndex(saved_quality)
        self.preview_fps, self.preview_render_scale = self.quality_combo.currentData()
        self.play_timer.setInterval(max(8, round(1000 / self.preview_fps)))
        self.gpu_check = QCheckBox()
        self.gpu_check.setChecked(False)
        self.gpu_check.setEnabled(GpuPreviewSurface is not None)
        self.gpu_check.setToolTip(
            "실험 기능입니다. 화면 표시만 OpenGL로 전환하며, 현재 소스 합성과 "
            "비주얼라이저 계산은 CPU에서 계속 처리됩니다."
        )
        self.timeline = PlaylistTimeline(tracks)
        self.timeline.setObjectName("previewTimeline")
        self.timeline.setRange(0, max(1, ceil(self._playlist_duration() * TIMELINE_SCALE)))
        self.time_label = QLabel()
        self.time_label.setObjectName("previewTimeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.track_time_label = QLabel("00:00 / 00:00")
        self.track_time_label.setObjectName("previewTimeLabel")
        self.track_title_label = QLabel("-")
        self.track_title_label.setObjectName("previewTrackTitle")
        self.track_title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.track_meta_label = QLabel("-")
        self.track_meta_label.setObjectName("mutedLabel")
        self.track_meta_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.track_counter_label = QLabel("0 / 0")
        self.track_counter_label.setObjectName("previewStatusChip")
        self.track_badge_label = QLabel("01")
        self.track_badge_label.setObjectName("previewTrackBadge")
        self.track_badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.track_badge_label.setFixedSize(48, 48)
        self.timeline_title_label = QLabel()
        self.timeline_title_label.setObjectName("panelTitle")
        self.shortcut_hint_label = QLabel()
        self.shortcut_hint_label.setObjectName("mutedLabel")
        self.shortcut_hint_label.setWordWrap(True)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        self.button_box.accepted.connect(self.accept)
        self.rejected.connect(self._stop_preview)
        self.accepted.connect(self._stop_preview)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        header_text.addWidget(self.dialog_title_label)
        header_text.addWidget(self.hint_label)
        header.addLayout(header_text, 1)
        header.addWidget(self.preview_mode_label)
        header.addWidget(self.frame_rate_label)
        layout.addLayout(header)

        self.preview_stage = QFrame()
        self.preview_stage.setObjectName("previewStage")
        stage_layout = QVBoxLayout(self.preview_stage)
        stage_layout.setContentsMargins(8, 8, 8, 8)
        stage_layout.addWidget(self.preview_stack_host, 1)
        layout.addWidget(self.preview_stage, 1)

        self.now_playing_card = QFrame()
        self.now_playing_card.setObjectName("previewInfoCard")
        now_layout = QHBoxLayout(self.now_playing_card)
        now_layout.setContentsMargins(14, 10, 14, 10)
        now_layout.setSpacing(12)
        now_layout.addWidget(self.track_badge_label)
        track_text = QVBoxLayout()
        track_text.setSpacing(1)
        track_text.addWidget(self.track_title_label)
        track_text.addWidget(self.track_meta_label)
        now_layout.addLayout(track_text, 1)
        now_layout.addWidget(self.track_counter_label)
        now_layout.addWidget(self.track_time_label)
        layout.addWidget(self.now_playing_card)

        self.timeline_card = QFrame()
        self.timeline_card.setObjectName("previewControlCard")
        timeline_layout = QVBoxLayout(self.timeline_card)
        timeline_layout.setContentsMargins(14, 10, 14, 8)
        timeline_header = QHBoxLayout()
        timeline_header.addWidget(self.timeline_title_label)
        timeline_header.addStretch()
        timeline_header.addWidget(self.time_label)
        timeline_layout.addLayout(timeline_header)
        timeline_layout.addWidget(self.timeline)
        layout.addWidget(self.timeline_card)

        self.transport_card = QFrame()
        self.transport_card.setObjectName("previewControlCard")
        controls = QGridLayout(self.transport_card)
        controls.setContentsMargins(14, 10, 14, 10)
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        controls.addWidget(self.previous_button, 0, 0)
        controls.addWidget(self.rewind_button, 0, 1)
        controls.addWidget(self.play_button, 0, 2)
        controls.addWidget(self.forward_button, 0, 3)
        controls.addWidget(self.next_button, 0, 4)
        controls.setColumnStretch(5, 1)
        controls.addWidget(self.quality_label, 0, 6)
        controls.addWidget(self.quality_combo, 0, 7)
        controls.addWidget(self.gpu_check, 0, 8)
        controls.addWidget(self.volume_label, 1, 0)
        controls.addWidget(self.volume_slider, 1, 1, 1, 3)
        controls.addWidget(self.volume_value_label, 1, 4)
        controls.addWidget(self.shortcut_hint_label, 1, 6, 1, 3)
        layout.addWidget(self.transport_card)
        layout.addWidget(self.button_box)

        self.timeline.valueChanged.connect(self._on_seeked)
        self.play_button.toggled.connect(self._toggle_playback)
        self.previous_button.clicked.connect(lambda: self._skip_track(-1))
        self.rewind_button.clicked.connect(lambda: self._seek_relative(-5.0))
        self.forward_button.clicked.connect(lambda: self._seek_relative(5.0))
        self.next_button.clicked.connect(lambda: self._skip_track(1))
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.quality_combo.currentIndexChanged.connect(self._set_preview_quality)
        self.gpu_check.toggled.connect(self._set_gpu_preview)
        self._install_shortcuts()
        self._set_gpu_preview(False)
        self._apply_preview_style()
        self.translator.language_changed.connect(self.retranslate)
        if self.source_store is not None:
            self.source_store.source_added.connect(self._invalidate_source_partitions)
            self.source_store.source_removed.connect(self._invalidate_source_partitions)
            self.source_store.source_changed.connect(self._invalidate_source_partitions)
            self.source_store.sources_replaced.connect(self._invalidate_source_partitions)
        self.retranslate()
        self.refresh_preview()

    def _track_at(self, playlist_seconds: float) -> tuple[int, PlaylistTrack, float, float] | None:
        """Return the active track plus local time and global start position."""
        cursor = 0.0
        previous: tuple[int, PlaylistTrack, float, float] | None = None
        for index, track in enumerate(self.tracks):
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            end = start + track.duration_seconds
            if start <= playlist_seconds < end:
                return index, track, max(0.0, min(track.duration_seconds, playlist_seconds - start)), start
            if playlist_seconds < start:
                return previous or (index, track, 0.0, start)
            previous = (index, track, track.duration_seconds, start)
            cursor = end
        return previous

    @staticmethod
    def _selection_has_audio(
        selected: tuple[int, PlaylistTrack, float, float] | None,
        playlist_seconds: float,
    ) -> bool:
        """Return whether a preview selection is inside audible track time."""
        if selected is None:
            return False
        _index, track, _elapsed, start = selected
        return start <= playlist_seconds < start + track.duration_seconds

    def _on_seeked(self, _value: int) -> None:
        if not self._advancing_playhead:
            self._playhead_seconds = self.timeline.value() / TIMELINE_SCALE
        self.refresh_preview()
        if self._playing and not self._advancing_playhead:
            self._start_audio_at_playhead()

    def refresh_preview(self) -> None:
        """Render the global playhead with the same dynamic-overlay drawing as export."""
        if self._closing:
            return
        selected = self._track_at(self.timeline.value() / TIMELINE_SCALE)
        if selected is None:
            return
        track_index, track, elapsed, start = selected
        phase, phase_progress, phase_duration = self._animation_state(track, elapsed)
        self._refresh_source_partitions()
        audio_dynamic_ids = set(self._cached_audio_dynamic_ids)
        canvas_dynamic_ids = set(self._cached_always_dynamic_ids)
        if phase is not None:
            canvas_dynamic_ids.update(self._cached_animated_source_ids)
        static_hidden_ids = frozenset(audio_dynamic_ids | canvas_dynamic_ids)
        refresh_base = (
            self._base_image.isNull()
            or self._base_track_id != track.id
            or self._base_hidden_source_ids != static_hidden_ids
        )
        if refresh_base:
            self._base_image = CanvasSnapshot.capture_track(
                self.scene, track, track_index + 1, len(self.tracks), start,
                None, 1.0, 0.0, audio_dynamic_ids,
                self._playlist_duration(), self.tracks, self.preview_render_scale,
                hide_source_ids=set(static_hidden_ids),
                timeline_seconds=self.timeline.value() / TIMELINE_SCALE,
            )
            self._base_track_id = track.id
            self._base_elapsed = 0.0
            self._base_hidden_source_ids = static_hidden_ids
            self._foreground_bands = []
        self._image = self._base_image.copy()
        if canvas_dynamic_ids:
            painter = QPainter(self._image)
            for source_ids, capture_rect in self._dynamic_capture_regions(canvas_dynamic_ids):
                buffer_key = frozenset(source_ids)
                buffer = self._dynamic_region_buffers.get(buffer_key, QImage())
                buffer = CanvasSnapshot.capture_track(
                    self.scene, track, track_index + 1, len(self.tracks), start,
                    phase, phase_progress, elapsed, audio_dynamic_ids,
                    self._playlist_duration(), self.tracks, self.preview_render_scale,
                    transparent=True,
                    hide_source_ids=set(self._cached_visible_source_ids - source_ids),
                    image_buffer=buffer,
                    capture_rect=capture_rect,
                    timeline_seconds=self.timeline.value() / TIMELINE_SCALE,
                    animation_phase_duration=phase_duration,
                )
                self._dynamic_region_buffers[buffer_key] = buffer
                painter.drawImage(
                    round(capture_rect.left() * self.preview_render_scale),
                    round(capture_rect.top() * self.preview_render_scale), buffer,
                )
            painter.end()
        # Dynamic audio layers are normally a cheap final composition.  When a
        # Canvas source sits above one of them, the compositor automatically
        # switches to the same multi-band Z pipeline used by export.
        self._composite_export_overlays(track, elapsed)
        self.time_label.setText(
            f"{format_timestamp(self.timeline.value() / TIMELINE_SCALE)} / "
            f"{format_timestamp(self._playlist_duration())}"
        )
        self.track_time_label.setText(
            f"{format_timestamp(elapsed)} / {format_timestamp(track.duration_seconds)}"
        )
        track_title = track.title or Path(track.file_path).stem
        self.track_title_label.setText(track_title)
        self.track_title_label.setToolTip(track_title)
        metadata = " / ".join(
            value for value in (track.artist, track.album) if value
        )
        self.track_meta_label.setText(metadata or Path(track.file_path).name)
        self.track_meta_label.setToolTip(str(Path(track.file_path)))
        self.track_counter_label.setText(f"{track_index + 1} / {len(self.tracks)}")
        self.track_badge_label.setText(f"{track_index + 1:02d}")
        self._update_pixmap()

    def _invalidate_source_partitions(self, *_args: object) -> None:
        """Invalidate preview caches only when the editor's source model changes."""
        self._source_partition_dirty = True
        self._base_image = QImage()
        self._base_track_id = ""
        self._dynamic_region_plans.clear()
        self._dynamic_region_buffers.clear()

    def _refresh_source_partitions(self) -> None:
        """Cache source partitions; rebuilding them per playback frame is wasteful."""
        if not self._source_partition_dirty:
            return
        time_tokens = (
            "%current_time%", "%track_current_time%", "%video_current_time%",
        )
        dynamic_types = {
            SourceType.LYRICS, SourceType.PROGRESS_BAR, SourceType.NOW_PLAYING,
        }
        audio_dynamic_ids: set[str] = set()
        always_dynamic_ids: set[str] = set()
        animated_source_ids: set[str] = set()
        visible_source_ids: set[str] = set()
        for item in self.scene.items():
            if (not isinstance(item, SourceItem) or not item.isVisible()
                    or not item.source.visible):
                continue
            source = item.source
            visible_source_ids.add(source.id)
            if source.source_type in {
                SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                SourceType.AUDIO_LEVEL_METER, SourceType.PARTICLE_OVERLAY,
            }:
                audio_dynamic_ids.add(source.id)
                continue
            if (source.source_type in dynamic_types
                    or source.source_type is SourceType.TIME
                    or source.timeline_start > 0.0
                    or source.timeline_duration > 0.0
                    or any(token in source.text.lower() for token in time_tokens)):
                always_dynamic_ids.add(source.id)
            if source.animation_in != "none" or source.animation_out != "none":
                animated_source_ids.add(source.id)
        self._cached_audio_dynamic_ids = frozenset(audio_dynamic_ids)
        self._cached_always_dynamic_ids = frozenset(always_dynamic_ids)
        self._cached_animated_source_ids = frozenset(animated_source_ids)
        self._cached_visible_source_ids = frozenset(visible_source_ids)
        self._dynamic_region_plans.clear()
        self._dynamic_region_buffers.clear()
        self._source_partition_dirty = False

    def _dynamic_capture_regions(self, dynamic_ids: set[str]) -> tuple[tuple[frozenset[str], QRectF], ...]:
        """Return non-overlapping dirty regions for the visible dynamic sources."""
        key = frozenset(dynamic_ids)
        cached = self._dynamic_region_plans.get(key)
        if cached is not None:
            return cached
        source_items = {
            item.source.id: item for item in self.scene.items()
            if isinstance(item, SourceItem) and item.source.id in dynamic_ids
        }
        groups: list[tuple[set[str], QRectF, float]] = []
        for source_id, item in source_items.items():
            source = item.source
            padding = max(8.0, source.outline_width)
            if source.shadow.enabled:
                padding += source.shadow.blur_radius + abs(source.shadow.offset_x) + abs(source.shadow.offset_y)
            if source.source_type is SourceType.LYRICS:
                padding += source.font_size + source.subtitle_line_spacing + source.subtitle_previous_blur + 8.0
            if source.source_type is SourceType.NOW_PLAYING:
                padding += 28.0
            if source.animation_in != "none" or source.animation_out != "none":
                padding += min(180.0, max(72.0, max(source.width, source.height) * 0.22)) + 8.0
            rect = item.sceneBoundingRect().adjusted(-padding, -padding, padding, padding)
            rect = rect.intersected(self.scene.artboard_rect)
            if rect.isEmpty():
                continue
            overlapping = [index for index, (_ids, group_rect, _z) in enumerate(groups) if group_rect.intersects(rect)]
            if not overlapping:
                groups.append(({source_id}, rect, source.z_index))
                continue
            merged_ids = {source_id}
            merged_rect = QRectF(rect)
            merged_z = source.z_index
            for index in reversed(overlapping):
                existing_ids, existing_rect, existing_z = groups.pop(index)
                merged_ids.update(existing_ids)
                merged_rect = merged_rect.united(existing_rect)
                merged_z = min(merged_z, existing_z)
            groups.append((merged_ids, merged_rect, merged_z))
        result = tuple(
            (frozenset(source_ids), QRectF(rect.toAlignedRect()))
            for source_ids, rect, _z in sorted(groups, key=lambda entry: entry[2])
        )
        self._dynamic_region_plans[key] = result
        return result

    def _composite_export_overlays(self, track: PlaylistTrack, elapsed: float,
                                   overlays: list[VisualizerOverlay] | None = None) -> None:
        """Composite pre-rendered Python overlay pixels without blocking the UI."""
        if self.visualizer_renderer is None or self._image.isNull():
            return
        try:
            active_overlays = overlays if overlays is not None else self.overlays
            timeline_seconds = self.timeline.value() / TIMELINE_SCALE
            active_overlays = [
                overlay for overlay in active_overlays
                if timeline_seconds >= overlay.timeline_start
                and (overlay.timeline_duration <= 0.0
                     or timeline_seconds < overlay.timeline_start + overlay.timeline_duration)
            ]
            if not active_overlays:
                return
            bands = max(max(4, overlay.bar_count) for overlay in self.overlays)
            self._ensure_track_analysis(track, bands)
            analyzed = self._track_levels.get(track.id)
            analysis = analyzed if analyzed is not None else self._idle_overlay_analysis(bands)
            frame_index = max(0, round(elapsed * self.preview_fps))
            scaled_overlays = tuple(
                replace(
                    overlay,
                    width=max(1, round(overlay.width * self.preview_render_scale)),
                    height=max(1, round(overlay.height * self.preview_render_scale)),
                )
                for overlay in active_overlays
            )
            self._request_overlay_frames(track.id, frame_index, scaled_overlays, analysis)
            layers = self._overlay_layers_for_frame(track.id, frame_index, len(scaled_overlays))
            if not layers:
                return
            if self._requires_z_band_composition(active_overlays):
                if self._composite_overlays_in_canvas_order(
                    track, elapsed, active_overlays, layers,
                ):
                    return
            painter = QPainter(self._image)
            for overlay, layer in zip(active_overlays, layers, strict=True):
                self._paint_overlay_layer(painter, overlay, layer)
            painter.end()
        except Exception:
            if not self._overlay_error_reported:
                LOGGER.warning("Preview overlay compositing failed", exc_info=True)
                self._overlay_error_reported = True
            return

    def _requires_z_band_composition(
        self, active_overlays: list[VisualizerOverlay],
    ) -> bool:
        """Return whether a Canvas source must be painted over an audio layer."""
        if not active_overlays:
            return False
        lowest_dynamic_z = min(overlay.z_index for overlay in active_overlays)
        audio_ids = set(self._cached_audio_dynamic_ids)
        return any(
            isinstance(item, SourceItem)
            and item.isVisible() and item.source.visible
            and item.source.id not in audio_ids
            and item.source.z_index >= lowest_dynamic_z
            for item in self.scene.items()
        )

    def _composite_overlays_in_canvas_order(
        self, track: PlaylistTrack, elapsed: float,
        active_overlays: list[VisualizerOverlay], layers: tuple[QImage, ...],
    ) -> bool:
        """Interleave preview overlays and Canvas bands exactly like export."""
        selected = self._track_at(self.timeline.value() / TIMELINE_SCALE)
        if selected is None:
            return False
        track_index, _selected_track, _selected_elapsed, start = selected
        phase, phase_progress, phase_duration = self._animation_state(track, elapsed)
        audio_ids = set(self._cached_audio_dynamic_ids)
        z_bands = CanvasSnapshot.z_bands(self.scene, audio_ids)
        if len(z_bands) < 2:
            return False
        common = dict(
            elapsed_seconds=elapsed,
            hide_visualizers=audio_ids,
            playlist_duration_seconds=self._playlist_duration(),
            playlist_tracks=self.tracks,
            output_scale=self.preview_render_scale,
            timeline_seconds=self.timeline.value() / TIMELINE_SCALE,
            animation_phase_duration=phase_duration,
        )
        base = CanvasSnapshot.capture_track(
            self.scene, track, track_index + 1, len(self.tracks), start,
            phase, phase_progress, z_max=z_bands[0][1], **common,
        )
        foreground_bands: list[tuple[float, QImage]] = []
        for z_min, z_max in z_bands[1:]:
            foreground = CanvasSnapshot.capture_track(
                self.scene, track, track_index + 1, len(self.tracks), start,
                phase, phase_progress, z_min=z_min, z_max=z_max,
                transparent=True, **common,
            )
            foreground_bands.append((
                z_min if z_min is not None else -10_000.0, foreground,
            ))
        self._foreground_bands = foreground_bands
        self._image = base
        entries: list[tuple[float, int, int]] = [
            (overlay.z_index, 0, index)
            for index, overlay in enumerate(active_overlays)
        ]
        entries.extend(
            (z_index, 1, index)
            for index, (z_index, _image) in enumerate(foreground_bands)
        )
        painter = QPainter(self._image)
        for _z_index, kind, index in sorted(entries):
            if kind == 0:
                self._paint_overlay_layer(
                    painter, active_overlays[index], layers[index],
                )
            else:
                painter.drawImage(0, 0, foreground_bands[index][1])
        painter.end()
        return True

    def _paint_overlay_layer(
        self, painter: QPainter, overlay: VisualizerOverlay, layer: QImage,
    ) -> None:
        """Paint one already-scaled audio-reactive layer at its Canvas transform."""
        scaled_width = layer.width()
        scaled_height = layer.height()
        if overlay.rotation:
            center_x = round(
                (overlay.x + overlay.width / 2) * self.preview_render_scale
            )
            center_y = round(
                (overlay.y + overlay.height / 2) * self.preview_render_scale
            )
            painter.save()
            painter.translate(center_x, center_y)
            painter.rotate(overlay.rotation)
            painter.drawImage(-scaled_width // 2, -scaled_height // 2, layer)
            painter.restore()
        else:
            painter.drawImage(
                round(overlay.x * self.preview_render_scale),
                round(overlay.y * self.preview_render_scale), layer,
            )

    def _schedule_refresh(self) -> None:
        """Coalesce worker completions into one GUI-thread preview composition."""
        if self._closing or self._refresh_queued:
            return
        self._refresh_queued = True
        QTimer.singleShot(0, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self) -> None:
        self._refresh_queued = False
        if not self._closing:
            self.refresh_preview()

    def _preview_worker_failed(self, message: str) -> None:
        """Retain a nonfatal worker error for diagnostics without closing playback."""
        LOGGER.warning("Preview background worker failed: %s", message)
        self.hint_label.setToolTip(message)

    def _idle_overlay_analysis(self, bands: int) -> dict[str, np.ndarray]:
        """Return a harmless placeholder until the real track analysis is ready."""
        normalized_bands = max(4, bands)
        cached = self._idle_overlay_analyses.get(normalized_bands)
        if cached is not None:
            return cached
        cached = {
            "levels": np.zeros((1, normalized_bands), dtype=np.float32),
            "waveform": np.zeros((1, max(32, normalized_bands)), dtype=np.float32),
        }
        self._idle_overlay_analyses[normalized_bands] = cached
        return cached

    def _request_overlay_frames(self, track_id: str, frame_index: int,
                                overlays: tuple[VisualizerOverlay, ...],
                                analysis: dict[str, np.ndarray]) -> None:
        """Keep a short, newest-first visualizer frame queue ready for presentation."""
        if self._overlay_worker is not None and self._overlay_worker.isRunning():
            if self._overlay_worker.track_id != track_id:
                self._overlay_worker.cancel()
            return
        end_frame = frame_index + self._overlay_prefetch_count
        missing_frame = next(
            (index for index in range(frame_index, end_frame)
             if (track_id, index) not in self._overlay_frame_cache),
            None,
        )
        if missing_frame is None:
            return
        self._overlay_worker = OverlayFrameWorker(
            track_id, self.preview_fps, self._overlay_generation, missing_frame,
            min(self._overlay_prefetch_count, end_frame - missing_frame), overlays, analysis, self,
        )
        self._overlay_worker.ready.connect(self._store_overlay_frames)
        self._overlay_worker.failed.connect(self._preview_worker_failed)
        self._overlay_worker.finished.connect(self._overlay_worker_finished)
        self._overlay_worker.start()

    def _store_overlay_frames(self, track_id: str, fps: int, generation: int, frames: object) -> None:
        """Receive completed QImage layers; no Canvas/QWidget access occurred in the worker."""
        if (fps != self.preview_fps or generation != self._overlay_generation
                or not isinstance(frames, list)):
            return
        for frame_index, layers in frames:
            if isinstance(frame_index, int) and isinstance(layers, tuple):
                self._overlay_frame_cache[(track_id, frame_index)] = layers
        self._trim_overlay_frame_cache(track_id)
        self._schedule_refresh()

    def _overlay_worker_finished(self) -> None:
        """Schedule the next small prefetch batch after the current worker exits."""
        worker = self.sender()
        if worker is self._overlay_worker:
            self._overlay_worker = None
        if isinstance(worker, OverlayFrameWorker):
            worker.deleteLater()
        self._schedule_refresh()

    def _overlay_layers_for_frame(self, track_id: str, frame_index: int,
                                  overlay_count: int) -> tuple[QImage, ...]:
        """Use the exact frame when ready, otherwise keep the newest completed frame."""
        layers = self._overlay_frame_cache.get((track_id, frame_index))
        if layers is not None and len(layers) == overlay_count:
            self._last_overlay_layers = layers
            self._last_overlay_track_id = track_id
            return layers
        if self._last_overlay_track_id == track_id and len(self._last_overlay_layers) == overlay_count:
            return self._last_overlay_layers
        prior_frames = [
            (cached_index, cached_layers)
            for (cached_track_id, cached_index), cached_layers in self._overlay_frame_cache.items()
            if cached_track_id == track_id and cached_index < frame_index
            and len(cached_layers) == overlay_count
        ]
        if prior_frames:
            _cached_index, cached_layers = max(prior_frames, key=lambda entry: entry[0])
            self._last_overlay_layers = cached_layers
            self._last_overlay_track_id = track_id
            return cached_layers
        return ()

    def _trim_overlay_frame_cache(self, active_track_id: str) -> None:
        """Bound cached QImages so a long playlist never accumulates frame memory."""
        selected = self._track_at(self.timeline.value() / TIMELINE_SCALE)
        current_frame = round(selected[2] * self.preview_fps) if selected else 0
        minimum = max(0, current_frame - 3)
        maximum = current_frame + self._overlay_prefetch_count * 3
        for key in list(self._overlay_frame_cache):
            track_id, frame_index = key
            if track_id != active_track_id or frame_index < minimum or frame_index > maximum:
                self._overlay_frame_cache.pop(key, None)

    def _clear_overlay_frames(self, track_id: str | None = None) -> None:
        """Discard stale prefetch output after a seek, track analysis, or quality change."""
        if (track_id is None or self._overlay_worker is None
                or self._overlay_worker.track_id == track_id):
            self._overlay_generation += 1
        if (self._overlay_worker is not None and self._overlay_worker.isRunning()
                and (track_id is None or self._overlay_worker.track_id == track_id)):
            self._overlay_worker.cancel()
        if track_id is None:
            self._overlay_frame_cache.clear()
            self._last_overlay_layers = ()
            self._last_overlay_track_id = ""
            return
        for key in [key for key in self._overlay_frame_cache if key[0] == track_id]:
            self._overlay_frame_cache.pop(key, None)
        if self._last_overlay_track_id == track_id:
            self._last_overlay_layers = ()
            self._last_overlay_track_id = ""

    def _ensure_track_analysis(self, track: PlaylistTrack, bands: int) -> None:
        """Analyze each selected track once, then reuse its moving FFT frames."""
        if track.id in self._track_levels or self.visualizer_renderer is None:
            return
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            return
        self._analysis_track_id = track.id
        self._analysis_worker = AudioAnalysisWorker(
            self.visualizer_renderer, track, bands, self.preview_fps,
            needs_waveform=any(overlay.kind == "waveform" for overlay in self.overlays),
            overlays=tuple(self.overlays),
            parent=self,
        )
        self._analysis_worker.ready.connect(self._store_track_levels)
        self._analysis_worker.failed.connect(self._preview_worker_failed)
        self._analysis_worker.finished.connect(self._analysis_finished)
        self._analysis_worker.start()

    def _store_track_levels(self, track_id: str, fps: int, levels: object) -> None:
        """Receive full-track FFT levels and refresh the current preview image."""
        if fps != self.preview_fps:
            return
        # Full-track FFT arrays are sizeable.  Retain only the two most recently
        # used tracks so long playlists cannot steadily exhaust memory.
        self._track_levels.pop(track_id, None)
        self._track_levels[track_id] = levels
        while len(self._track_levels) > 2:
            oldest_track_id = next(iter(self._track_levels))
            self._track_levels.pop(oldest_track_id, None)
        self._clear_overlay_frames(track_id)
        self._schedule_refresh()

    def _analysis_finished(self) -> None:
        worker = self.sender()
        if worker is self._analysis_worker:
            self._analysis_worker = None
            self._analysis_track_id = ""
        if isinstance(worker, AudioAnalysisWorker):
            worker.deleteLater()
        self._schedule_refresh()

    def _toggle_playback(self, playing: bool) -> None:
        self._playing = playing
        if playing:
            self._playhead_seconds = self.timeline.value() / TIMELINE_SCALE
            self._reset_frame_statistics()
            self._start_audio_at_playhead()
            self.play_clock.start()
            self.play_timer.start()
        else:
            self.play_timer.stop()
            self.media_player.pause()
        self._set_play_text()

    def _install_shortcuts(self) -> None:
        """Install familiar transport shortcuts for the preview window."""
        bindings = (
            ("Space", self.play_button.toggle),
            ("Left", lambda: self._seek_relative(-5.0)),
            ("Right", lambda: self._seek_relative(5.0)),
            ("Shift+Left", lambda: self._skip_track(-1)),
            ("Shift+Right", lambda: self._skip_track(1)),
            ("Up", lambda: self._adjust_volume(5)),
            ("Down", lambda: self._adjust_volume(-5)),
        )
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _seek_relative(self, seconds: float) -> None:
        value = self.timeline.value() + round(seconds * TIMELINE_SCALE)
        self.timeline.setValue(max(self.timeline.minimum(), min(self.timeline.maximum(), value)))

    def _skip_track(self, offset: int) -> None:
        selected = self._track_at(self.timeline.value() / TIMELINE_SCALE)
        if selected is None:
            return
        index = max(0, min(len(self.tracks) - 1, selected[0] + offset))
        cursor = 0.0
        for current_index, track in enumerate(self.tracks):
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            if current_index == index:
                self.timeline.setValue(round(start * TIMELINE_SCALE))
                return
            cursor = start + track.duration_seconds

    def _adjust_volume(self, change: int) -> None:
        self.volume_slider.setValue(max(0, min(100, self.volume_slider.value() + change)))

    def _set_volume(self, value: int) -> None:
        value = save_preview_volume(value)
        self.audio_output.setVolume(value / 100.0)
        self.volume_value_label.setText(f"{value}%")

    def _set_preview_quality(self, _index: int) -> None:
        """Change preview frame cadence and rebuild audio analysis at that cadence."""
        fps, scale = self.quality_combo.currentData()
        QSettings().setValue("preview_quality_index", self.quality_combo.currentIndex())
        if fps == self.preview_fps and scale == self.preview_render_scale:
            return
        self.preview_fps = fps
        self.preview_render_scale = scale
        self._base_image = QImage()
        self._base_track_id = ""
        self.play_timer.setInterval(max(8, round(1000 / fps)))
        self._reset_frame_statistics()
        self._track_levels.clear()
        self._clear_overlay_frames()
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            self._analysis_worker.cancel()
        self.refresh_preview()

    def _set_gpu_preview(self, enabled: bool) -> None:
        """Switch to the opt-in, experimental OpenGL presentation surface."""
        if enabled and not self.gpu_preview_enabled:
            korean = self.translator.language is Language.KOREAN
            message = (
                "이 기능은 베타입니다. 화면 표시만 GPU(OpenGL)로 전환하며, "
                "캔버스 합성·텍스트·비주얼라이저 분석은 계속 CPU에서 처리됩니다.\n\n"
                "성능 향상이 보장되지 않으며, 화면 이상 또는 종료 현상이 있으면 "
                "이 옵션을 끄고 CPU 미리보기를 사용하세요."
                if korean else
                "This is a beta feature. Only frame presentation switches to GPU (OpenGL); "
                "canvas composition, text, and visualizer analysis still run on the CPU.\n\n"
                "Performance gains are not guaranteed. If you see artifacts or instability, "
                "turn this option off and use CPU preview."
            )
            answer = QMessageBox.warning(
                self,
                "GPU 미리보기 (베타)" if korean else "GPU preview (Beta)",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                blocker = QSignalBlocker(self.gpu_check)
                self.gpu_check.setChecked(False)
                del blocker
                return
        if enabled and self.gpu_surface is None and GpuPreviewSurface is not None:
            self.gpu_surface = GpuPreviewSurface()
            self.preview_stack.addWidget(self.gpu_surface)
        self.gpu_preview_enabled = enabled and self.gpu_surface is not None
        self.preview_stack.setCurrentIndex(1 if self.gpu_preview_enabled else 0)
        korean = self.translator.language is Language.KOREAN
        self.preview_mode_label.setText(
            ("GPU · 베타" if korean else "GPU · Beta")
            if self.gpu_preview_enabled else ("CPU 모드" if korean else "CPU mode")
        )
        self._update_pixmap()

    def _start_audio_at_playhead(self) -> None:
        playlist_seconds = self.timeline.value() / TIMELINE_SCALE
        selected = self._track_at(playlist_seconds)
        if selected is None:
            return
        self._playhead_seconds = playlist_seconds
        if not self._selection_has_audio(selected, playlist_seconds):
            # `_track_at` intentionally retains the nearest track so Canvas text
            # and artwork remain meaningful in a silent gap. Audio transport must
            # nevertheless remain stopped until the real start boundary.
            self.media_player.stop()
            self._active_track_index = -1
            self._last_media_position_ms = 0
            return
        index, track, elapsed, _start = selected
        if index != self._active_track_index:
            self._active_track_index = index
            self.media_player.setSource(QUrl.fromLocalFile(str(Path(track.file_path).resolve())))
        self.media_player.setPosition(round(elapsed * 1000))
        self._last_media_position_ms = round(elapsed * 1000)
        self.media_player.play()

    def _advance_playback(self) -> None:
        if not self._playing:
            return
        elapsed_milliseconds = max(1, self.play_clock.restart())
        previous_seconds = self._playhead_seconds
        selected_before = self._track_at(previous_seconds)
        audio_was_active = self._selection_has_audio(
            selected_before, previous_seconds
        )
        predicted_seconds = self._playhead_seconds + elapsed_milliseconds / 1000.0
        player_milliseconds = self.media_player.position()
        if (audio_was_active and selected_before is not None and player_milliseconds > 0
                and player_milliseconds != self._last_media_position_ms):
            self._last_media_position_ms = player_milliseconds
            audio_seconds = selected_before[3] + player_milliseconds / 1000.0
            # QMediaPlayer commonly reports position at roughly 10 Hz.  Use it
            # only to correct meaningful drift; the precise timer supplies the
            # intermediate 30/60 FPS playhead positions.
            if abs(audio_seconds - predicted_seconds) > 0.18:
                predicted_seconds = audio_seconds
        self._playhead_seconds = max(0.0, predicted_seconds)
        next_value = round(self._playhead_seconds * TIMELINE_SCALE)
        if next_value > self.timeline.maximum():
            self.play_button.setChecked(False)
            self.timeline.setValue(self.timeline.maximum())
            return
        old_index = self._track_at(self.timeline.value() / TIMELINE_SCALE)
        self._advancing_playhead = True
        try:
            self.timeline.setValue(next_value)
        finally:
            self._advancing_playhead = False
        new_index = self._track_at(next_value / TIMELINE_SCALE)
        audio_is_active = self._selection_has_audio(
            new_index, next_value / TIMELINE_SCALE
        )
        if (
            old_index and new_index
            and (old_index[0] != new_index[0] or audio_was_active != audio_is_active)
        ):
            self._start_audio_at_playhead()

    def _animation_state(
        self, track: PlaylistTrack, elapsed: float,
    ) -> tuple[str | None, float, float]:
        """Use the same bounded track-animation windows as final export."""
        sources = [item.source for item in self.scene.items() if isinstance(item, SourceItem)]
        intro = min(
            track.duration_seconds / 2,
            max(
                (source.animation_in_duration for source in sources
                 if source.animation_in != "none"),
                default=0.0,
            ),
        )
        outro = min(
            max(0.0, track.duration_seconds - intro) / 2,
            max(
                (source.animation_out_duration for source in sources
                 if source.animation_out != "none"),
                default=0.0,
            ),
        )
        if intro > 0 and elapsed < intro:
            return "in", elapsed / intro, intro
        if outro > 0 and elapsed > max(0.0, track.duration_seconds - outro):
            return (
                "out", (elapsed - (track.duration_seconds - outro)) / outro,
                outro,
            )
        return None, 1.0, 0.0

    def _playlist_duration(self) -> float:
        cursor = 0.0
        total = 0.0
        for track in self.tracks:
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            cursor = start + track.duration_seconds
            total = max(total, cursor)
        return total

    def _update_pixmap(self) -> None:
        if self._image.isNull():
            return
        if self.gpu_preview_enabled and self.gpu_surface is not None:
            # The OpenGL surface queues painting internally, so count the latest
            # submitted frame in beta GPU mode. CPU mode counts actual paint events.
            self._record_presented_frame()
            self.gpu_surface.set_image(self._image)
            return
        self.preview_label.set_image(self._image)

    def _reset_frame_statistics(self) -> None:
        """Restart the rolling frame-rate measurement after a quality change."""
        self._frame_stats_clock.restart()
        self._presented_frames = 0
        self._actual_preview_fps = 0.0
        self._update_frame_rate_label()

    def _record_presented_frame(self) -> None:
        """Show the cadence actually achieved by CPU/GPU frame presentation."""
        if not self._playing:
            return
        self._presented_frames += 1
        elapsed = self._frame_stats_clock.elapsed()
        if elapsed < 500:
            return
        self._actual_preview_fps = self._presented_frames * 1000.0 / elapsed
        self._frame_stats_clock.restart()
        self._presented_frames = 0
        self._update_frame_rate_label()

    def _update_frame_rate_label(self) -> None:
        korean = self.translator.language is Language.KOREAN
        actual = "--" if self._actual_preview_fps <= 0 else f"{self._actual_preview_fps:.0f}"
        self.frame_rate_label.setText(
            f"실제 {actual} / 목표 {self.preview_fps} FPS"
            if korean else f"Actual {actual} / Target {self.preview_fps} FPS"
        )
        self.frame_rate_label.setToolTip(
            "실제 표시 FPS입니다. 목표 FPS보다 낮으면 장면 합성 또는 화면 표시가 병목입니다."
            if korean else
            "Actual displayed FPS. A value below the target means composition or presentation is the bottleneck."
        )

    def _set_play_text(self) -> None:
        korean = self.translator.language is Language.KOREAN
        self.play_button.setText(
            "Ⅱ  일시정지" if self._playing and korean else
            "Ⅱ  Pause" if self._playing else
            "▶  재생" if korean else "▶  Play"
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_pixmap()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_preview()
        super().closeEvent(event)

    def _stop_preview(self) -> None:
        """Stop local media and wait briefly for cancellable frame workers to finish."""
        if self._closing:
            return
        self._closing = True
        self._refresh_queued = False
        self._playing = False
        self.play_timer.stop()
        self.media_player.stop()
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            self._analysis_worker.cancel()
            self._finish_or_detach_worker(self._analysis_worker)
        if self._overlay_worker is not None and self._overlay_worker.isRunning():
            self._overlay_worker.cancel()
            self._finish_or_detach_worker(self._overlay_worker)

    def _apply_preview_style(self) -> None:
        """Apply a compact card hierarchy that remains readable in both themes."""
        dark = self.palette().color(self.backgroundRole()).lightness() < 128
        panel = "#1C222C" if dark else "#FFFFFF"
        field = "#121820" if dark else "#F4F7FB"
        border = "#303947" if dark else "#D7E0EA"
        text = "#EAF1F8" if dark else "#18212D"
        muted = "#9AAABD" if dark else "#64748B"
        hover = "#293649" if dark else "#E7EEF6"
        self.setStyleSheet(
            f"""
            #previewDialogTitle {{ color: {text}; font-size: 20px; font-weight: 700; padding: 0; }}
            #previewStage {{ background: #0B1017; border: 1px solid {border}; border-radius: 12px; }}
            #previewInfoCard, #previewControlCard {{ background: {panel}; border: 1px solid {border}; border-radius: 10px; }}
            #previewTrackTitle {{ color: {text}; font-size: 16px; font-weight: 700; padding: 0; }}
            #previewTrackBadge {{ background: #1685D1; color: #FFFFFF; border-radius: 12px; font-size: 15px; font-weight: 800; }}
            #previewStatusChip {{ background: {field}; color: {muted}; border: 1px solid {border}; border-radius: 8px; padding: 6px 10px; }}
            #previewInfoCard QLabel#mutedLabel {{ color: {muted}; }}
            #previewTimeLabel, #previewValueLabel {{ color: {text}; font-weight: 600; }}
            #previewPlayButton {{ background: #1685D1; color: #FFFFFF; border: 1px solid #1685D1; border-radius: 9px; min-width: 104px; min-height: 24px; font-weight: 700; }}
            #previewPlayButton:hover {{ background: #0D72B8; border-color: #0D72B8; }}
            #previewPlayButton:checked {{ background: #C2415B; border-color: #C2415B; }}
            #previewTransportButton {{ background: {field}; color: {text}; border: 1px solid {border}; border-radius: 8px; min-width: 42px; min-height: 24px; }}
            #previewTransportButton:hover {{ background: {hover}; border-color: #55B8FF; }}
            #previewTimeline::groove:horizontal {{ background: {field}; border: 1px solid {border}; border-radius: 4px; height: 8px; }}
            #previewTimeline::sub-page:horizontal {{ background: #1685D1; border-radius: 4px; }}
            #previewTimeline::handle:horizontal {{ background: #FFFFFF; border: 2px solid #1685D1; width: 16px; margin: -5px 0; border-radius: 8px; }}
            QSlider::groove:horizontal {{ background: {field}; border-radius: 3px; height: 6px; }}
            QSlider::sub-page:horizontal {{ background: #1685D1; border-radius: 3px; }}
            QSlider::handle:horizontal {{ background: #1685D1; width: 14px; margin: -4px 0; border-radius: 7px; }}
            """
        )

    def refresh_theme(self) -> None:
        """Rebuild dialog-local cards when the application theme changes."""
        self._apply_preview_style()

    @staticmethod
    def _finish_or_detach_worker(worker: QThread) -> None:
        """Prevent Qt from destroying a rare slow worker during dialog teardown."""
        if worker.wait(5000):
            return
        LOGGER.warning("Preview worker did not stop within five seconds; detaching safely")
        application = QApplication.instance()
        if application is not None:
            worker.setParent(application)
            worker.finished.connect(worker.deleteLater)

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("전체 재생 미리보기" if korean else "Playlist playback preview")
        self.dialog_title_label.setText(
            "전체 재생 미리보기" if korean else "Playlist Preview"
        )
        self.hint_label.setText(
            "실제 음원과 내보내기 구성을 전체 타임라인에서 확인합니다."
            if korean else
            "Review the complete timeline with actual audio and export-equivalent composition."
        )
        self.timeline_title_label.setText(
            "전체 플레이리스트" if korean else "Full playlist"
        )
        self.shortcut_hint_label.setText(
            "Space 재생/일시정지  ·  ←/→ 5초 이동  ·  Shift+←/→ 곡 이동  ·  ↑/↓ 볼륨"
            if korean else
            "Space Play/Pause  ·  ←/→ Seek 5s  ·  Shift+←/→ Change track  ·  ↑/↓ Volume"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Close).setText("닫기" if korean else "Close")
        self.volume_label.setText("볼륨" if korean else "Volume")
        self.quality_label.setText("품질" if korean else "Quality")
        quality_labels = (
            ("낮음 · 15 FPS", "일반 · 30 FPS", "높음 · 45 FPS", "매우 높음 · 60 FPS")
            if korean else
            ("Low · 15 FPS", "Normal · 30 FPS", "High · 45 FPS", "Very high · 60 FPS")
        )
        for index, label in enumerate(quality_labels):
            self.quality_combo.setItemText(index, label)
        self.quality_combo.setToolTip(
            "낮음 15fps · 일반 30fps · 높음 45fps · 매우 높음 60fps\n"
            "높은 품질은 텍스트와 카드 애니메이션도 더 부드럽게 하지만 CPU 사용량이 증가합니다."
            if korean else
            "Low 15fps · Normal 30fps · High 45fps · Very high 60fps\n"
            "Higher quality also smooths text and card animation, but uses more CPU."
        )
        self.gpu_check.setText("GPU 미리보기 (베타)" if korean else "GPU preview (Beta)")
        self.gpu_check.setToolTip(
            "실험 기능: 화면 표시만 OpenGL로 전환합니다. 실제 미리보기 합성은 CPU 기반입니다."
            if korean else
            "Experimental: only frame presentation uses OpenGL. Preview composition remains CPU-based."
        )
        self.preview_mode_label.setText(
            ("GPU · 베타" if korean else "GPU · Beta")
            if self.gpu_preview_enabled else ("CPU 모드" if korean else "CPU mode")
        )
        self.previous_button.setText("|◀")
        self.rewind_button.setText("−5s")
        self.forward_button.setText("+5s")
        self.next_button.setText("▶|")
        self.previous_button.setToolTip("이전 곡 (Shift+←)" if korean else "Previous track (Shift+←)")
        self.next_button.setToolTip("다음 곡 (Shift+→)" if korean else "Next track (Shift+→)")
        self.rewind_button.setToolTip("5초 뒤로 (←)" if korean else "Back 5 seconds (←)")
        self.forward_button.setToolTip("5초 앞으로 (→)" if korean else "Forward 5 seconds (→)")
        self._update_frame_rate_label()
        self._set_play_text()
