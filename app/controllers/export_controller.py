"""Export frame-staging, UI-lock, and notification orchestration.

Extracted from MainWindow: the frame-staging bookkeeping, main-form
lock/unlock, system-tray notifications, and storage-monitor plumbing that
surround an export run. MainWindow keeps identically-named thin wrapper
methods that delegate here.

Named ExportOrchestrator (not ExportController) because
app/services/export_controller.py already defines a small, stateless
ExportController used for pure work-mode/output-validation policy
decisions shared by the UI and renderer -- a distinct, narrower concern
from this class's MainWindow-coupled orchestration. They are kept separate
rather than merged.

The core `_export_video` render orchestration (this file's `export_video`
method and its direct helpers) also lives here now. FFmpegRenderer,
RenderWorker, and ExportSettingsDialog are looked up lazily from
app.ui.main_window at call time (not imported at module scope) because
existing tests patch them at "app.ui.main_window.<Name>" -- importing
them here directly would silently stop those patches from intercepting
real FFmpeg/encoder construction during tests.

FFmpeg install/catalog management (_load_ffmpeg_catalog and friends) is
left in MainWindow: it is a distinct concern (installing/selecting an
ffmpeg.exe) from exporting a project with one already configured.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QImage, QImageReader, QImageWriter
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle, QSystemTrayIcon

from app.models.source import Source, SourceType
from app.preview.album_art import (
    adjust_personal_color, extract_track_cover, extract_track_personal_color,
)
from app.preview.export_plan import build_export_plan, canvas_render_scale
from app.preview.export_session import ExportSession, PngStaging
from app.dialogs.export_complete_dialog import ExportCompleteDialog
from app.dialogs.export_progress_dialog import ExportProgressDialog
from app.renderer.ffmpeg_renderer import (
    EncoderUnavailableError,
    ExportMetadata,
    FFmpegNotFoundError,
    RenderCancelledError,
    RenderError,
    RenderFrame,
    RenderResult,
    RenderSettings,
    VideoClipOverlay,
    VisualizerOverlay,
    WORK_MODE_AUTO,
    WORK_MODE_MAX_SPEED,
    WORK_MODE_STABLE,
)
from app.renderer.png_frame_staging import (
    PngFrameStagingCancelled,
    PngFrameStagingError,
    PngFrameStagingPipeline,
)
from app.services.app_settings_service import AppSettings, VIDEO_ENCODERS
from app.services.export_controller import ExportController
from app.services.export_storage_service import ExportStorageMonitor, estimate_export_storage
from app.services.playlist_service import PlaylistService
from app.timeline.compiler import compile_playlist
from app.services.video_encoder_service import (
    AUTO_VIDEO_ENCODER,
    CPU_H264_ENCODER,
    NVIDIA_H264_ENCODER,
    VideoEncoderAdvisor,
)
from app.utils.i18n import Language
from app.utils.logging_setup import report_unexpected_error
from app.video.timeline import build_video_occurrences

if TYPE_CHECKING:
    from app.renderer.ffmpeg_renderer import FFmpegRenderer
    from app.ui.main_window import MainWindow

LOGGER = logging.getLogger(__name__)


class ExportOrchestrator:
    """Own export frame-staging, UI lock, and notification plumbing for a MainWindow."""

    def __init__(self, window: "MainWindow") -> None:
        self.window = window

    # -- frame staging -------------------------------------------------

    def stage_frame(
        self, image: QImage, duration_seconds: float, stream_key: str = "base",
    ) -> RenderFrame:
        """Stage a frame synchronously or queue it to the active PNG pipeline."""
        from app.ui.main_window import ExportFrameStagingMetrics

        window = self.window
        if window._export_frame_staging is None:
            raise RenderError("Export frame staging has not been initialized.")
        if image.isNull():
            raise RenderError("Could not stage an empty export frame on disk.")
        window._export_capture_count += 1
        if window._export_frame_metrics is None:
            window._export_frame_metrics = ExportFrameStagingMetrics()
        window._export_frame_metrics.record_capture()
        previous = window._export_frame_cache.get(stream_key)
        if previous is not None and image == previous[0]:
            window._export_frame_metrics.record_reuse()
            return RenderFrame(previous[1], max(0.001, duration_seconds))
        # Disk usage queries are surprisingly expensive on synced/network-backed
        # Windows temp drives. Check periodically instead of once per PNG.
        if window._export_frame_index % 32 == 0:
            free_space = shutil.disk_usage(window._export_frame_staging.name).free
            minimum_free = max(512 * 1024 * 1024, image.width() * image.height() * 8)
            if free_space < minimum_free:
                raise RenderError(
                    "Not enough temporary disk space to safely prepare export frames. "
                    "Free at least 1 GB on the system temporary drive and try again."
                )
        path = Path(window._export_frame_staging.name) / f"frame_{window._export_frame_index:07d}.png"
        window._export_frame_index += 1
        owned_image = image.copy()
        pipeline = window._export_png_pipeline
        if pipeline is not None:
            try:
                pipeline.submit(owned_image, path, stream_key)
            except PngFrameStagingCancelled as error:
                raise RenderCancelledError(str(error)) from error
            except PngFrameStagingError as error:
                raise RenderError(str(error)) from error
        else:
            writer = QImageWriter(str(path), b"png")
            # Compression level 1 trades a little temporary disk space for much
            # faster preparation. FFmpeg output quality is unaffected.
            writer.setCompression(1)
            writer.setOptimizedWrite(False)
            if not writer.write(owned_image):
                raise RenderError(
                    f"Could not stage an export frame on disk: {writer.errorString()}"
                )
            try:
                staged_bytes = path.stat().st_size
            except OSError as error:
                # Diagnostics must never turn a successfully written export frame
                # into an export failure on an unusual or transient filesystem.
                LOGGER.warning("Could not measure staged export frame %s: %s", path, error)
                staged_bytes = 0
            window._export_frame_metrics.record_file(
                stream_key, owned_image, staged_bytes,
            )
        window._export_frame_cache[stream_key] = (owned_image, path)
        return RenderFrame(path, max(0.001, duration_seconds))

    def start_png_pipeline(
        self, cancel_event: "threading.Event", *, queue_capacity: int = 3,
    ) -> None:
        """Start bounded PNG writes so Canvas capture can continue concurrently."""
        from app.ui.main_window import ExportFrameStagingMetrics

        window = self.window
        if window._export_png_pipeline is not None:
            raise RenderError("Export PNG staging is already active.")
        if window._export_frame_metrics is None:
            window._export_frame_metrics = ExportFrameStagingMetrics()

        def record_written(stream_key: str, image: QImage, byte_count: int) -> None:
            metrics = window._export_frame_metrics
            if metrics is not None:
                metrics.record_file(stream_key, image, byte_count)

        window._export_png_pipeline = PngFrameStagingPipeline(
            record_written,
            cancel_event=cancel_event,
            wait_callback=QApplication.processEvents,
            queue_capacity=max(1, queue_capacity),
        )

    def finish_png_pipeline(self) -> None:
        window = self.window
        pipeline = window._export_png_pipeline
        if pipeline is None:
            return
        window._export_png_pipeline = None
        try:
            pipeline.finish()
        except PngFrameStagingCancelled as error:
            raise RenderCancelledError(str(error)) from error
        except PngFrameStagingError as error:
            raise RenderError(str(error)) from error
        LOGGER.info(
            "PNG frame staging pipeline drained: peak_buffered_frames=%d",
            pipeline.peak_buffered_frames,
        )

    def cancel_png_pipeline(self) -> None:
        window = self.window
        pipeline = window._export_png_pipeline
        window._export_png_pipeline = None
        if pipeline is not None:
            pipeline.cancel()

    @staticmethod
    def animation_sample_rate(output_fps: int) -> int:
        """Sample Canvas motion at the exact frame rate selected for export."""
        return max(1, min(240, int(output_fps)))

    def clear_frame_staging(self) -> None:
        """Release disk-backed captured frames after every export completion path."""
        window = self.window
        window._stop_export_storage_monitor()
        window._cancel_export_png_pipeline()
        if window._export_frame_metrics is not None:
            summary = window._export_frame_metrics.snapshot()
            window._last_export_frame_metrics = summary
            LOGGER.info(
                "Export frame staging summary: captures=%d files=%d reused=%d "
                "bytes=%d largest=%d (%dx%d) elapsed=%.3fs streams=%s",
                summary.capture_count,
                summary.unique_file_count,
                summary.reused_frame_count,
                summary.total_bytes,
                summary.largest_file_bytes,
                summary.largest_width,
                summary.largest_height,
                summary.elapsed_seconds,
                {
                    key: {
                        "files": summary.stream_file_counts[key],
                        "bytes": summary.stream_bytes.get(key, 0),
                    }
                    for key in sorted(summary.stream_file_counts)
                },
            )
        if window._export_frame_staging is not None:
            window._export_frame_staging.cleanup()
            window._export_frame_staging = None
        window._export_frame_index = 0
        window._export_capture_count = 0
        window._export_frame_cache.clear()
        window._export_frame_metrics = None

    # -- main-form lock --------------------------------------------------

    def lock_main_form(self) -> None:
        """Block every main-form interaction while an export is in flight."""
        window = self.window
        if window._export_ui_lock_state is not None:
            return
        central_widget = window.centralWidget()
        menu_bar = window.menuBar()
        window._export_ui_lock_state = (
            central_widget.isEnabled(),
            menu_bar.isEnabled(),
            window.toolbar.isEnabled(),
            window.export_action.isEnabled(),
            window.acceptDrops(),
        )
        central_widget.setEnabled(False)
        menu_bar.setEnabled(False)
        window.toolbar.setEnabled(False)
        window.export_action.setEnabled(False)
        window.setAcceptDrops(False)

    def unlock_main_form(self) -> None:
        """Restore the main form after every successful, failed, or cancelled export."""
        window = self.window
        window._export_restore_pending = False
        state = window._export_ui_lock_state
        if state is None:
            return
        window._export_ui_lock_state = None
        central_enabled, menu_enabled, toolbar_enabled, export_enabled, accepts_drops = state
        window.centralWidget().setEnabled(central_enabled)
        window.menuBar().setEnabled(menu_enabled)
        window.toolbar.setEnabled(toolbar_enabled)
        window.export_action.setEnabled(export_enabled)
        window.setAcceptDrops(accepts_drops)

    # -- notifications -------------------------------------------------

    @staticmethod
    def notification_allowed(
        settings: AppSettings,
        step: str,
        application_active: bool,
    ) -> bool:
        """Apply the master, per-stage, and focus notification preferences."""
        if not settings.export_notifications_enabled:
            return False
        enabled_for_step = {
            "visuals": settings.export_notify_visuals,
            "audio": settings.export_notify_audio,
            "effects": settings.export_notify_effects,
            "encode": settings.export_notify_encode,
            "complete": settings.export_notify_complete,
            "failures": settings.export_notify_failures,
        }.get(step, False)
        if not enabled_for_step:
            return False
        return (
            settings.export_notification_mode == "always"
            or not application_active
        )

    def sync_notification_tray(self, settings: AppSettings) -> None:
        """Create a tray endpoint only while export notifications are enabled."""
        window = self.window
        if not settings.export_notifications_enabled:
            if window._notification_tray is not None:
                window._notification_tray.hide()
                window._notification_tray.deleteLater()
                window._notification_tray = None
            return
        window._ensure_notification_tray()

    def ensure_notification_tray(self) -> QSystemTrayIcon | None:
        window = self.window
        if window._notification_tray is not None:
            return window._notification_tray
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = QApplication.windowIcon()
        if icon.isNull():
            icon = window.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        tray = QSystemTrayIcon(icon, window)
        tray.setToolTip("Playlist Canvas")
        tray.messageClicked.connect(window._restore_from_export_notification)
        tray.show()
        window._notification_tray = tray
        return tray

    def restore_from_notification(self) -> None:
        """Bring the running export or completed workspace back to the user."""
        window = self.window
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()
        if window._export_dialog is not None:
            window._export_restore_pending = False
            window._export_dialog.show()
            window._export_dialog.raise_()
            window._export_dialog.activateWindow()

    def show_system_notification(
        self,
        title: str,
        message: str,
        *,
        critical: bool = False,
    ) -> bool:
        window = self.window
        tray = window._ensure_notification_tray()
        if tray is None:
            return False
        icon = (
            QSystemTrayIcon.MessageIcon.Critical
            if critical else QSystemTrayIcon.MessageIcon.Information
        )
        tray.showMessage(title, message, icon, 7000)
        return True

    def notify_stage(self, stage: str) -> None:
        """Notify once when the export crosses into a user-facing phase."""
        window = self.window
        step = ExportProgressDialog._stage_key(stage)
        if step in window._export_notified_steps:
            return
        window._export_notified_steps.add(step)
        settings = window.settings_service.current
        application_active = (
            QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        if not window._export_notification_allowed(
            settings, step, application_active,
        ):
            return
        korean = window.translator.language is Language.KOREAN
        names = {
            "visuals": "화면 준비" if korean else "Visual preparation",
            "audio": "오디오 준비" if korean else "Audio preparation",
            "effects": "효과 준비" if korean else "Effects preparation",
            "encode": "영상 만들기" if korean else "Creating video",
            "complete": "내보내기 완료" if korean else "Export complete",
        }
        name = names.get(step)
        if name is None:
            return
        output_name = (
            window._active_export_output_path.name
            if window._active_export_output_path is not None else ""
        )
        if step == "complete":
            title = "내보내기 완료" if korean else "Export complete"
            message = (
                f"{output_name} 파일을 만들었습니다."
                if korean else f"Created {output_name}."
            )
        else:
            title = "내보내기 진행" if korean else "Export progress"
            message = (
                f"{name} 단계를 시작했습니다."
                if korean else f"Started: {name}."
            )
        window._show_system_notification(title, message)

    def notify_problem(self, message: str, *, cancelled: bool = False) -> None:
        window = self.window
        settings = window.settings_service.current
        application_active = (
            QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        if not window._export_notification_allowed(
            settings, "failures", application_active,
        ):
            return
        korean = window.translator.language is Language.KOREAN
        if cancelled:
            title = "내보내기 취소" if korean else "Export cancelled"
            detail = (
                "진행 중인 내보내기를 안전하게 취소했습니다."
                if korean else "The active export was cancelled safely."
            )
        else:
            title = "내보내기 오류" if korean else "Export failed"
            detail = message.strip().replace("\n", " ")[:220]
        window._show_system_notification(
            title, detail, critical=not cancelled,
        )

    def handle_render_progress(
        self,
        export_dialog: ExportProgressDialog,
        stage: str,
        fraction: float,
        message: str,
    ) -> None:
        """Keep progress UI, status activity, and notifications synchronized."""
        from app.ui.main_window import EXPORT_PREPARATION_PROGRESS_WEIGHT

        window = self.window
        overall = (
            EXPORT_PREPARATION_PROGRESS_WEIGHT
            + (1.0 - EXPORT_PREPARATION_PROGRESS_WEIGHT) * fraction
        )
        export_dialog.update_progress(stage, overall, message)
        window.activity_progress.update(
            "export", overall, f"{stage} · {message}",
        )
        window._notify_export_stage(stage)

    @staticmethod
    def format_bytes(count: int) -> str:
        value = float(max(0, count))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024.0:
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} TB"

    # -- storage monitor -------------------------------------------------

    def start_storage_monitor(self, output_path: str | Path) -> None:
        """Track export-owned files without walking large folders on the UI thread."""
        window = self.window
        window._stop_export_storage_monitor()
        monitor = ExportStorageMonitor(output_path, window)
        if window._export_frame_staging is not None:
            monitor.set_path("frames", window._export_frame_staging.name)
        monitor.snapshot_ready.connect(window._handle_export_storage_snapshot)
        window._export_storage_monitor = monitor
        monitor.start()
        dialog = window._export_dialog
        if dialog is not None:
            dialog.cancel_requested.connect(window._freeze_export_storage_monitor)

    def freeze_storage_monitor(self) -> None:
        """Stop live storage sampling the instant a cancel is confirmed.

        Cancellation stops the encoders quickly, but a still-polling monitor kept
        showing the last few numbers and made the export look like it was still
        writing. Halt sampling immediately; cleanup still runs in stop_...().
        """
        monitor = self.window._export_storage_monitor
        if monitor is not None:
            monitor.stop()

    def handle_storage_path(
        self, kind: str, path: str | Path | None,
    ) -> None:
        monitor = self.window._export_storage_monitor
        if monitor is not None:
            monitor.set_path(kind, path)

    def handle_storage_snapshot(self, snapshot: object) -> None:
        dialog = self.window._export_dialog
        if dialog is not None:
            dialog.update_storage_snapshot(snapshot)

    def stop_storage_monitor(self) -> None:
        window = self.window
        monitor = window._export_storage_monitor
        window._export_storage_monitor = None
        if monitor is None:
            return
        monitor.stop()
        if not monitor.wait(3000):
            LOGGER.warning("Export storage monitor did not stop within three seconds.")
        monitor.deleteLater()

    # -- staging space / preflight ---------------------------------------

    def prepare_staging_space(
        self, render_settings: RenderSettings, duration_seconds: float,
        layer_count: int, use_streamed_visuals: bool, korean: bool,
    ) -> bool:
        """Redirect frame staging to the output drive if the temp drive is short,
        and warn before starting when neither drive has comfortable room.

        Returns ``False`` only when the user declines to continue anyway.
        """
        window = self.window
        raw_frame_bytes = max(
            1, render_settings.output_width * render_settings.output_height * 3
        )
        seconds = max(0.0, duration_seconds)
        # ffv1 / libx264rgb (or deflated PNGs) on Canvas content: conservatively
        # ~40% of raw RGB, once per visual layer.
        intermediate = int(
            raw_frame_bytes * render_settings.fps * seconds
            * 0.4 * max(1, layer_count if use_streamed_visuals else 1)
        )
        final_video = int(raw_frame_bytes * render_settings.fps * seconds * 0.08)
        temp_need = int(intermediate * 1.3)
        output_need = int(intermediate * 0.4) + final_video

        def free_bytes(location: Path) -> int | None:
            try:
                return shutil.disk_usage(location).free
            except OSError:
                return None

        system_temp = Path(tempfile.gettempdir())
        output_parent = (
            window._active_export_output_path.parent
            if window._active_export_output_path is not None else None
        )
        staging_dir: Path | None = None
        temp_free = free_bytes(system_temp)
        if (
            temp_free is not None
            and temp_free < int(temp_need * 1.15)
            and output_parent is not None
        ):
            output_free = free_bytes(output_parent)
            if (
                output_free is not None
                and output_free > int((temp_need + output_need) * 1.2)
            ):
                staging_dir = output_parent
                LOGGER.info(
                    "Staging export frames on the output drive (%s); the system "
                    "temporary drive is short on space.", output_parent,
                )
        window._clear_export_frame_staging()
        window._export_frame_staging = TemporaryDirectory(
            prefix="playlist-video-frames-",
            dir=str(staging_dir) if staging_dir is not None else None,
        )
        window._export_frame_index = 0

        checks = [(Path(window._export_frame_staging.name), temp_need)]
        if output_parent is not None:
            checks.append((output_parent, output_need))
        shortfalls: list[str] = []
        for location, required in checks:
            free = free_bytes(location)
            if free is not None and free < int(required * 1.15):
                drive = location.anchor or str(location)
                shortfalls.append(
                    f"{drive}  —  {window._format_bytes(free)} free / "
                    f"~{window._format_bytes(required)} needed"
                )
        if not shortfalls:
            return True
        detail = "\n".join(shortfalls)
        answer = QMessageBox.warning(
            window,
            "저장 공간 부족 가능성" if korean else "Low disk space",
            (
                "무손실 중간 파일과 최종 영상을 저장할 임시/출력 공간이 부족할 수 "
                "있습니다. 내보내는 도중 공간이 모자라면 실패할 수 있습니다.\n\n"
                f"{detail}\n\n그래도 계속 진행할까요?"
                if korean else
                "The temporary or output drive may not have enough room for the "
                "lossless intermediate files and the final video, so the export "
                "could fail partway through.\n\n"
                f"{detail}\n\nContinue anyway?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def report_preparation_progress(
        self, fraction: float, detail: str,
    ) -> None:
        """Show one Canvas-preparation progress update from the export session."""
        window = self.window
        if window._export_dialog is None:
            return
        window._export_dialog.update_progress(
            "Preparing visual frames", fraction, detail,
        )
        window.activity_progress.update("export", fraction, detail)

    def cancel_active_session(self) -> None:
        """Stop the running export session's encoders and PNG pipeline, if any."""
        window = self.window
        session = window._active_export_session
        window._active_export_session = None
        if session is not None:
            session.cancel_streams()
        else:
            window._cancel_export_png_pipeline()

    def resolve_render_settings(
        self, renderer: "FFmpegRenderer", requested_app_settings: AppSettings,
        output: str, save_as_default: bool,
    ) -> tuple[list, RenderSettings, AppSettings, str, bool] | None:
        """Resolve the encoder and run the export preflight.

        Returns ``(active_tracks, render_settings, effective_app_settings,
        encoder_label, automatic)`` on success, or ``None`` when the user
        cancelled or the preflight failed (a dialog was already shown).
        """
        window = self.window
        korean = window.translator.language is Language.KOREAN
        automatic_encoder = (
            requested_app_settings.video_codec == AUTO_VIDEO_ENCODER
        )
        effective_encoder = (
            VideoEncoderAdvisor.automatic_encoder()
            if automatic_encoder else requested_app_settings.video_codec
        )
        selected_app_settings = replace(
            requested_app_settings, video_codec=effective_encoder,
        )
        active_tracks = [track for track in window.playlist_service.tracks if track.enabled]
        if not active_tracks:
            QMessageBox.warning(
                window,
                "내보내기 오류" if korean else "Export error",
                "내보낼 음악을 하나 이상 선택하세요."
                if korean else "Select at least one music track to export.",
            )
            return None
        width, height = selected_app_settings.resolution
        selected_app_settings = replace(
            selected_app_settings,
            # Workload is selected from the actual export cost so the user
            # does not need to tune a machine-specific setting before export.
            work_mode=ExportController.choose_work_mode(
                width, height, selected_app_settings.fps,
            ),
        )
        render_settings = selected_app_settings.render_settings()
        try:
            renderer.preflight_export(active_tracks, output, render_settings)
        except EncoderUnavailableError as error:
            if not (
                automatic_encoder
                and effective_encoder == NVIDIA_H264_ENCODER
            ):
                QMessageBox.critical(
                    window,
                    "내보내기 사전 검사 실패" if korean else "Export preflight failed",
                    str(error),
                )
                return None
            answer = QMessageBox.warning(
                window,
                "NVIDIA 인코더 사용 실패" if korean else "NVIDIA encoder failed",
                (
                    "NVIDIA GPU를 감지하여 NVENC 인코더를 자동으로 시도했지만 "
                    "사용할 수 없습니다. 그래픽 드라이버, 다른 프로그램의 GPU 인코딩 "
                    "사용 또는 FFmpeg 호환성 문제일 수 있습니다.\n\n"
                    f"오류 내용:\n{error}\n\n"
                    "CPU H.264 인코더로 다시 검사하고 내보내기를 계속할까요? "
                    "속도는 느릴 수 있지만 결과 영상의 해상도와 FPS는 유지됩니다."
                )
                if korean else
                (
                    "An NVIDIA GPU was detected and NVENC was tried automatically, "
                    "but it could not be used. The GPU driver, another application's "
                    "encoder session, or FFmpeg compatibility may be the cause.\n\n"
                    f"Error:\n{error}\n\n"
                    "Retry the preflight and continue export with CPU H.264? It may "
                    "be slower, but the output resolution and FPS will be preserved."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            effective_encoder = CPU_H264_ENCODER
            selected_app_settings = replace(
                requested_app_settings, video_codec=effective_encoder,
            )
            render_settings = selected_app_settings.render_settings()
            try:
                renderer.preflight_export(active_tracks, output, render_settings)
            except RenderError as cpu_error:
                QMessageBox.critical(
                    window,
                    "CPU 인코더 검사 실패" if korean else "CPU encoder check failed",
                    str(cpu_error),
                )
                return None
        except RenderError as error:
            QMessageBox.critical(
                window, "내보내기 사전 검사 실패" if korean else "Export preflight failed",
                str(error),
            )
            return None
        if save_as_default:
            # Preserve "Automatic" as the preference; the concrete encoder is
            # selected again for the hardware available at the next export.
            window.settings_service.save(requested_app_settings)
        encoder_name = next(
            (
                label for label, codec in VIDEO_ENCODERS.items()
                if codec == selected_app_settings.video_codec
            ),
            selected_app_settings.video_codec,
        )
        if automatic_encoder:
            encoder_name = (
                f"자동 선택 → {encoder_name}"
                if korean else f"Automatic → {encoder_name}"
            )
        return (
            active_tracks, render_settings, selected_app_settings,
            encoder_name, automatic_encoder,
        )

    # -- core export orchestration -----------------------------------------

    def export_video(self) -> None:
        """Render the static Canvas and enabled playlist tracks to an MP4 file."""
        # FFmpegRenderer, RenderWorker, and ExportSettingsDialog are looked up
        # from app.ui.main_window (not imported at module scope) because
        # existing tests patch "app.ui.main_window.<Name>" to avoid touching
        # real FFmpeg/encoder hardware during a test run.
        from app.ui.main_window import ExportSettingsDialog, FFmpegRenderer, RenderWorker

        window = self.window
        korean = window.translator.language is Language.KOREAN
        try:
            configured_path = window.settings_service.current.ffmpeg_path or None
            renderer = FFmpegRenderer(configured_path)
        except FFmpegNotFoundError:
            QMessageBox.warning(
                window,
                "FFmpeg 필요" if korean else "FFmpeg required",
                (
                    "영상을 내보내려면 FFmpeg 설치가 필요합니다.\n\n"
                    "확인을 누르면 설정의 FFmpeg 설치 화면으로 이동합니다. "
                    "자동 설치를 사용하거나 기존 ffmpeg.exe를 선택해 주세요."
                )
                if korean else (
                    "FFmpeg is required to export a video.\n\n"
                    "Click OK to open the FFmpeg setup page. Use automatic "
                    "installation or select an existing ffmpeg executable."
                ),
            )
            window._show_settings(focus_ffmpeg=True)
            return
        active_tracks = [track for track in window.playlist_service.tracks if track.enabled]
        if not active_tracks:
            QMessageBox.warning(window, "Export error", "Select at least one music track to export.")
            return
        invalid_track = next(
            (track for track in active_tracks if track.duration_seconds <= 0.0), None
        )
        if invalid_track is not None:
            QMessageBox.warning(
                window,
                "음원 길이 오류" if korean else "Invalid audio duration",
                (f"'{invalid_track.title}' 곡의 길이를 확인할 수 없습니다. "
                 "FFmpeg 설정과 원본 음원을 확인한 뒤 다시 추가해 주세요.")
                if korean else
                (f"The duration of '{invalid_track.title}' could not be determined. "
                 "Check FFmpeg and the source audio, then add the track again."),
            )
            return
        output_directory = window.settings_service.current.output_directory
        default_directory = (
            Path(output_directory) if output_directory
            else window.settings_service.default_output_directory()
        )
        export_options = ExportSettingsDialog(
            window.settings_service.current,
            len(active_tracks),
            window._playlist_duration(active_tracks),
            window.translator,
            default_directory / "playlist.mp4",
            window,
            canvas_size=(
                round(window.canvas.scene_model.artboard_rect.width()),
                round(window.canvas.scene_model.artboard_rect.height()),
            ),
            estimated_layer_count=min(3, max(1, len(window.store.sources()))),
        )
        if export_options.exec() != export_options.DialogCode.Accepted:
            return
        requested_app_settings = export_options.app_settings
        quality_profile_name = export_options.quality_mode_combo.currentText()
        output = str(export_options.output_path)
        resolved = window._resolve_export_render_settings(
            renderer, requested_app_settings, output,
            export_options.save_as_default,
        )
        if resolved is None:
            return
        (
            active_tracks, render_settings, selected_app_settings,
            encoder_name, automatic_encoder,
        ) = resolved
        render_scale = canvas_render_scale(
            window.canvas.scene_model, render_settings,
        )
        upscale_warnings = window._export_upscale_warnings(
            active_tracks, render_settings, render_scale, korean,
        )
        if upscale_warnings:
            listed = "\n".join(upscale_warnings[:5])
            if QMessageBox.question(
                window,
                "낮은 해상도 이미지" if korean else "Low-resolution images",
                (
                    "다음 이미지 소스는 원본 해상도보다 크게 표시되어 흐릿할 수 "
                    f"있습니다:\n\n{listed}\n\n더 큰 이미지를 사용하면 화질이 "
                    "좋아집니다. 그대로 내보낼까요?"
                    if korean else
                    "These image sources are shown larger than their source "
                    f"resolution and may look soft:\n\n{listed}\n\nUsing larger "
                    "images improves quality. Export anyway?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        font_warnings = window._export_font_warnings(korean)
        if font_warnings:
            listed = "\n".join(font_warnings[:5])
            if QMessageBox.question(
                window,
                "대체 폰트 확인" if korean else "Check fallback fonts",
                (
                    "다음 텍스트 요소의 폰트를 내보내기 환경에서 찾지 못할 수 있습니다:\n\n"
                    f"{listed}\n\n계속 내보낼까요?"
                    if korean else
                    "These text elements may use a fallback font during export:\n\n"
                    f"{listed}\n\nContinue export?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            ) != QMessageBox.StandardButton.Yes:
                return
        work_mode_name = {
            WORK_MODE_STABLE: "안정" if korean else "Stable",
            WORK_MODE_AUTO: "자동" if korean else "Automatic",
            WORK_MODE_MAX_SPEED: "최대 속도" if korean else "Maximum speed",
        }.get(
            render_settings.work_mode,
            "자동" if korean else "Automatic",
        )
        settings_summary = (
            (
                f"해상도 {render_settings.output_width} × {render_settings.output_height}"
                f" · {selected_app_settings.fps} FPS\n"
                f"비디오 인코더 {encoder_name}\n"
                f"작업 모드 {work_mode_name} · "
                f"품질 모드 {quality_profile_name} · CRF {selected_app_settings.crf}"
                f" · 인코딩 속도 {selected_app_settings.preset}"
                f" · 오디오 AAC {selected_app_settings.audio_bitrate}"
            )
            if korean else
            (
                f"Resolution {render_settings.output_width} × {render_settings.output_height}"
                f" · {selected_app_settings.fps} FPS\n"
                f"Video encoder {encoder_name}\n"
                f"Work mode {work_mode_name} · "
                f"Quality mode {quality_profile_name} · CRF {selected_app_settings.crf}"
                f" · Encoding speed {selected_app_settings.preset}"
                f" · Audio AAC {selected_app_settings.audio_bitrate}"
            )
        )
        window._export_notified_steps.clear()
        window._active_export_output_path = Path(output).expanduser().resolve()
        window._pending_export_result = None
        preparation_cancel = threading.Event()
        window._export_preparation_cancel = preparation_cancel
        window._export_dialog = ExportProgressDialog(window)
        window._export_dialog.set_korean(korean)
        window._export_dialog.set_export_details(
            len(active_tracks), window._playlist_duration(active_tracks),
            settings_summary, output,
        )
        initial_storage_estimate = estimate_export_storage(
            render_settings.output_width, render_settings.output_height,
            render_settings.fps, window._playlist_duration(active_tracks),
            selected_app_settings.crf, selected_app_settings.audio_bitrate,
            min(3, max(1, len(window.store.sources()))),
        )
        window._export_dialog.set_storage_estimate(initial_storage_estimate)
        window._export_dialog.set_busy(
            "Preparing visual frames",
            "캔버스와 애니메이션 프레임을 준비하고 있습니다."
            if korean else "Capturing Canvas and animation frames.",
        )
        request_preparation_cancel = preparation_cancel.set
        window._export_dialog.cancel_requested.connect(request_preparation_cancel)
        window._export_dialog.minimize_requested.connect(
            window._minimize_during_export
        )
        window._export_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        window._lock_main_form_for_export()
        window.activity_progress.begin(
            "export", "영상 내보내기" if korean else "Exporting video",
            detail="화면 프레임 준비 중" if korean else "Preparing visual frames",
        )
        window._notify_export_stage("Preparing visual frames")
        window.statusBar().showMessage(
            "화면 프레임 준비 중..." if korean else "Preparing visual frames..."
        )
        try:
            window._export_dialog.show()
            QApplication.processEvents()
            window._clear_export_frame_staging()
            window._export_frame_staging = TemporaryDirectory(
                prefix="playlist-video-frames-"
            )
            window._export_frame_index = 0
        except Exception as error:
            window._export_preparation_cancel = None
            window._clear_export_frame_staging()
            if window._export_dialog:
                window._export_dialog.complete(False)
                window._export_dialog = None
            window._unlock_main_form_after_export()
            window.activity_progress.finish("export")
            report_unexpected_error("Starting export preparation", error)
            window._notify_export_problem(str(error))
            window._active_export_output_path = None
            QMessageBox.critical(
                window, "내보내기 오류" if korean else "Export error", str(error)
            )
            window._resume_close_after_export_cancel()
            return
        window._active_export_session = None
        try:
            animation_fps = window._export_animation_sample_rate(render_settings.fps)
            playlist_duration = window._playlist_duration(active_tracks)
            visualizers = window._export_visualizers(active_tracks, render_scale)
            video_clips = window._export_video_clips(
                active_tracks, playlist_duration, render_settings.work_mode,
                render_scale,
            )
            plan = build_export_plan(
                window.canvas.scene_model, active_tracks, window.store.sources(),
                render_settings, playlist_duration, visualizers, video_clips,
                renderer, animation_fps,
            )
            if not window._prepare_export_staging_space(
                render_settings, playlist_duration, len(plan.z_bands),
                plan.use_streamed_visuals, korean,
            ):
                raise RenderCancelledError("Export cancelled at the disk-space check.")
            actual_storage_estimate = estimate_export_storage(
                render_settings.output_width, render_settings.output_height,
                render_settings.fps, playlist_duration,
                selected_app_settings.crf, selected_app_settings.audio_bitrate,
                max(1, len(plan.z_bands)),
            )
            window._export_dialog.set_storage_estimate(actual_storage_estimate)
            window._start_export_storage_monitor(output)
            stream_root = (
                Path(window._export_frame_staging.name)
                if plan.use_streamed_visuals and window._export_frame_staging is not None
                else None
            )
            export_session = ExportSession(
                scene=window.canvas.scene_model,
                renderer=renderer,
                plan=plan,
                render_settings=render_settings,
                active_tracks=active_tracks,
                stream_root=stream_root,
                preparation_cancel=preparation_cancel,
                korean=korean,
                staging=PngStaging(
                    stage_frame=window._stage_export_frame,
                    start_pipeline=lambda capacity: window._start_export_png_pipeline(
                        preparation_cancel, queue_capacity=capacity,
                    ),
                    finish_pipeline=window._finish_export_png_pipeline,
                    cancel_pipeline=window._cancel_export_png_pipeline,
                    pending_frames=lambda: (
                        window._export_png_pipeline.pending_frames
                        if window._export_png_pipeline is not None else 0
                    ),
                    queue_capacity=window._export_png_queue_capacity(render_settings),
                ),
                layer_worker_count=lambda count: window._export_layer_worker_count(
                    render_settings, count,
                ),
                report_progress=window._report_export_preparation_progress,
                pump_ui=QApplication.processEvents,
            )
            window._active_export_session = export_session
            artifacts = export_session.run()
            frames = artifacts.frames
            static_layers = artifacts.static_layers
        except RenderCancelledError:
            window._cancel_active_export_session()
            window._export_preparation_cancel = None
            window._clear_export_frame_staging()
            if window._export_dialog:
                window._export_dialog.complete(False)
                window._export_dialog = None
            window._unlock_main_form_after_export()
            window.activity_progress.finish("export")
            window._notify_export_problem(
                "Export preparation was cancelled.", cancelled=True,
            )
            window._active_export_output_path = None
            window.statusBar().showMessage(
                "내보내기를 취소했습니다." if korean else "Export cancelled.", 5000
            )
            window._resume_close_after_export_cancel()
            return
        except RenderError as error:
            window._cancel_active_export_session()
            window._export_preparation_cancel = None
            window._clear_export_frame_staging()
            if window._export_dialog:
                window._export_dialog.complete(False)
                window._export_dialog = None
            window._unlock_main_form_after_export()
            window.activity_progress.finish("export")
            window._notify_export_problem(str(error))
            window._active_export_output_path = None
            QMessageBox.critical(
                window, "내보내기 오류" if korean else "Export error", str(error)
            )
            window._resume_close_after_export_cancel()
            return
        except Exception as error:
            window._cancel_active_export_session()
            window._export_preparation_cancel = None
            window._clear_export_frame_staging()
            if window._export_dialog:
                window._export_dialog.complete(False)
                window._export_dialog = None
            window._unlock_main_form_after_export()
            window.activity_progress.finish("export")
            report_unexpected_error("Preparing export frames", error)
            window._notify_export_problem(str(error))
            window._active_export_output_path = None
            QMessageBox.critical(
                window, "내보내기 오류" if korean else "Export error", str(error)
            )
            window._resume_close_after_export_cancel()
            return
        window._export_preparation_cancel = None
        try:
            window._export_dialog.cancel_requested.disconnect(request_preparation_cancel)
        except (RuntimeError, TypeError):
            pass
        if korean:
            window._export_dialog.setWindowTitle("내보내기 진행 상황")
            window._export_dialog.cancel_button.setText("취소")
            window._export_dialog.stage_label.setText("내보내기 준비 중")
        project_title = window.project_settings.title.strip()
        export_metadata = ExportMetadata(
            title=(
                "" if project_title in ("", "Untitled Project", "제목 없는 프로젝트")
                else project_title
            ),
            artist=window.project_settings.author.strip(),
            comment=window.project_settings.description.strip(),
        )
        window._render_worker = RenderWorker(
            renderer,
            frames,
            window.playlist_service.tracks,
            output,
            render_settings,
            visualizers,
            static_layers,
            video_clips,
            export_metadata,
        )
        export_dialog = window._export_dialog
        window._render_worker.progress.connect(
            lambda stage, fraction, message: window._handle_export_render_progress(
                export_dialog, stage, fraction, message,
            )
        )
        storage_path_signal = getattr(
            window._render_worker, "storage_path_changed", None,
        )
        if storage_path_signal is not None:
            storage_path_signal.connect(window._handle_export_storage_path)
        window._render_worker.succeeded.connect(window._export_succeeded)
        window._render_worker.failed.connect(window._export_failed)
        window._render_worker.cancelled.connect(window._export_cancelled)
        window._render_worker.finished.connect(window._export_finished)
        window._export_dialog.cancel_requested.connect(window._render_worker.cancel)
        window.statusBar().showMessage("렌더링 중..." if korean else "Rendering...")
        window._render_worker.start()

    def minimize_during_export(self) -> None:
        """Minimize the app while keeping preparation or rendering active."""
        window = self.window
        if window._export_dialog is None or window._export_ui_lock_state is None:
            return
        window._export_restore_pending = True
        window._export_dialog.hide()
        window.showMinimized()

    def restore_dialog_after_minimize(self) -> None:
        """Bring the modal progress UI back when the taskbar window is restored."""
        window = self.window
        if not window._export_restore_pending or window.isMinimized():
            return
        window._export_restore_pending = False
        if window._export_dialog is None or window._export_ui_lock_state is None:
            return
        window._export_dialog.show()
        window._export_dialog.raise_()
        window._export_dialog.activateWindow()

    @staticmethod
    def playlist_duration(tracks: list) -> float:
        """Return the full timeline duration, including any user-created gaps."""
        return compile_playlist(tracks, enabled_only=True).duration_seconds

    def upscale_warnings(
        self, active_tracks: list, render_settings: RenderSettings,
        render_scale: float, korean: bool,
    ) -> list[str]:
        """List raster sources FFmpeg would visibly enlarge past their pixels.

        Vector content is now rasterised at the export resolution, so a
        low-resolution album cover or imported image is the remaining soft spot.
        A source is flagged once its on-screen area exceeds 1.5x its native area.
        """
        window = self.window
        output_w = max(1, render_settings.output_width)
        output_h = max(1, render_settings.output_height)

        def native_size(path: str) -> tuple[int, int]:
            if not path:
                return (0, 0)
            size = QImageReader(path).size()
            return (max(0, size.width()), max(0, size.height()))

        # Every album-art source shares the track covers; rate the weakest one.
        cover_native = (0, 0)
        for track in active_tracks:
            pixmap = extract_track_cover(track.file_path, track.cover_path)
            if pixmap.isNull():
                continue
            current = (pixmap.width(), pixmap.height())
            cover_native = current if cover_native == (0, 0) else (
                min(cover_native[0], current[0]),
                min(cover_native[1], current[1]),
            )

        warnings: list[str] = []
        for source in window.store.sources():
            if not source.visible:
                continue
            stype = source.source_type
            element_shown = (
                round(source.width * source.scale * render_scale),
                round(source.height * source.scale * render_scale),
            )
            if stype in {SourceType.IMAGE, SourceType.LOGO, SourceType.WATERMARK}:
                native, shown = native_size(source.content_path), element_shown
            elif stype is SourceType.ALBUM_COVER:
                native = (
                    native_size(source.content_path)
                    if source.content_path else cover_native
                )
                shown = element_shown
            elif stype is SourceType.BACKGROUND and source.background_mode == "image":
                native, shown = native_size(source.content_path), (output_w, output_h)
            elif stype is SourceType.BACKGROUND and source.background_mode == "album_art":
                native, shown = cover_native, (output_w, output_h)
            else:
                continue
            if native[0] <= 0 or native[1] <= 0:
                continue
            scale_up = (
                shown[0] * shown[1] / (native[0] * native[1])
            ) ** 0.5
            if scale_up < 1.5:
                continue
            label = source.name or stype.value
            warnings.append(
                f"· {label}: {native[0]}×{native[1]} → "
                + (
                    f"약 {shown[0]}×{shown[1]} ({scale_up:.1f}배 확대)"
                    if korean else
                    f"about {shown[0]}×{shown[1]} ({scale_up:.1f}x upscale)"
                )
            )
        return warnings

    def font_warnings(self, korean: bool) -> list[str]:
        """Detect custom fonts that are missing or no longer loadable."""
        window = self.window
        installed = set(QFontDatabase.families())
        warnings: list[str] = []
        for source in window.store.sources():
            if not source.visible or not source.font_family:
                continue
            if source.font_path and not Path(source.font_path).is_file():
                warnings.append(
                    f"· {source.name or 'Text'}: {source.font_family} (font file missing)"
                    if not korean else
                    f"· {source.name or '텍스트'}: {source.font_family} (폰트 파일 없음)"
                )
            elif source.font_family not in installed:
                warnings.append(
                    f"· {source.name or 'Text'}: {source.font_family} (not installed)"
                    if not korean else
                    f"· {source.name or '텍스트'}: {source.font_family} (설치되지 않음)"
                )
        return warnings

    def visualizers(
        self, tracks: list | None = None, render_scale: float = 1.0,
    ) -> list[VisualizerOverlay]:
        """Translate visible, axis-aligned Canvas visualizers into Python-rendered overlays.

        ``render_scale`` (>= 1.0) maps Canvas coordinates onto the export's final
        pixel grid so an overlay stays aligned with the up-rendered base stream.
        """
        window = self.window
        overlays: list[VisualizerOverlay] = []
        visualizer_sources = [
            source for source in window.store.sources()
            if source.source_type in {
                SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                SourceType.AUDIO_LEVEL_METER, SourceType.PARTICLE_OVERLAY,
            } and source.visible
        ]
        active_tracks = list(tracks) if tracks is not None else [
            track for track in window.playlist_service.tracks if track.enabled
        ]
        track_personal_colors = (
            tuple(
                extract_track_personal_color(track.file_path, track.cover_path)
                for track in active_tracks
            )
            if any(source.personal_color_enabled for source in visualizer_sources)
            else ()
        )
        for source in visualizer_sources:
            kind = {
                SourceType.AUDIO_VISUALIZER: "visualizer",
                SourceType.AUDIO_WAVEFORM: "waveform",
                SourceType.AUDIO_LEVEL_METER: "level_meter",
                SourceType.PARTICLE_OVERLAY: "particles",
            }[source.source_type]
            overlay_width = max(8, round(source.width * source.scale))
            overlay_height = max(8, round(source.height * source.scale))
            # QGraphicsItem scales around its centre.  Use the same transformed
            # top-left point for FFmpeg, otherwise scaled visualizers drift down
            # and right compared with the Canvas placement.  ``render_scale``
            # then lifts every measure onto the export's pixel grid (identity at
            # 1.0 since ``round(int * 1.0) == int``).
            overlay_x = round(source.x + (source.width - overlay_width) / 2.0)
            overlay_y = round(source.y + (source.height - overlay_height) / 2.0)
            overlays.append(VisualizerOverlay(
                x=round(overlay_x * render_scale),
                y=round(overlay_y * render_scale),
                width=max(8, round(overlay_width * render_scale)),
                height=max(8, round(overlay_height * render_scale)),
                style=source.visualizer_style,
                color=source.fill_color,
                personal_colors=(
                    tuple(
                        adjust_personal_color(
                            color,
                            source.fill_color,
                            brightness=source.personal_color_brightness,
                            saturation=source.personal_color_saturation,
                            hue_shift=source.personal_color_hue_shift,
                            strength=source.personal_color_strength,
                        )
                        for color in track_personal_colors
                    )
                    if source.personal_color_enabled else ()
                ),
                opacity=source.opacity,
                bar_count=source.visualizer_bars,
                line_width=source.visualizer_line_width * render_scale,
                sensitivity=source.visualizer_sensitivity,
                reactivity=source.visualizer_reactivity,
                noise_gate=source.visualizer_noise_gate,
                min_level=source.visualizer_min_level,
                max_level=source.visualizer_max_level,
                attack=source.visualizer_attack,
                release=source.visualizer_release,
                smoothing=source.visualizer_smoothing,
                curve=source.visualizer_curve,
                kind=kind,
                effect_style=(source.waveform_style if kind == "waveform" else
                              source.level_meter_style if kind == "level_meter" else
                              source.particle_style if kind == "particles" else source.visualizer_style),
                density=source.particle_density,
                speed=source.particle_speed,
                level_meter_mode=(
                    "stereo" if source.level_meter_mode == "led" else source.level_meter_mode
                ),
                level_meter_style=(
                    "led" if source.level_meter_mode == "led" else source.level_meter_style
                ),
                level_meter_orientation=source.level_meter_orientation,
                level_meter_sensitivity=source.level_meter_sensitivity,
                level_meter_attack=source.level_meter_attack,
                level_meter_release=source.level_meter_release,
                level_meter_min_level=source.level_meter_min_level,
                level_meter_max_level=source.level_meter_max_level,
                level_meter_segments=source.level_meter_segments,
                level_meter_gap=source.level_meter_gap * render_scale,
                level_meter_show_peak=source.level_meter_show_peak,
                level_meter_peak_hold=source.level_meter_peak_hold,
                level_meter_peak_decay=source.level_meter_peak_decay,
                level_meter_track_color=source.level_meter_track_color,
                level_meter_low_color=source.level_meter_low_color,
                level_meter_mid_color=source.level_meter_mid_color,
                level_meter_high_color=source.level_meter_high_color,
                particle_min_size=source.particle_min_size * render_scale,
                particle_max_size=source.particle_max_size * render_scale,
                particle_opacity=source.particle_opacity,
                particle_direction=source.particle_direction,
                particle_drift=source.particle_drift,
                particle_twinkle=source.particle_twinkle,
                particle_glow=source.particle_glow,
                particle_secondary_color=source.particle_secondary_color,
                particle_seed=source.particle_seed,
                rotation=source.rotation,
                z_index=source.z_index,
                timeline_start=source.timeline_start,
                timeline_duration=source.timeline_duration,
                animation_in=source.animation_in,
                animation_out=source.animation_out,
                animation_in_duration=source.animation_in_duration,
                animation_out_duration=source.animation_out_duration,
            ))
        # FFmpeg overlays later inputs on top.  Preserve the Canvas stacking
        # order when two reactive sources overlap.
        return sorted(overlays, key=lambda overlay: (overlay.z_index, overlay.y, overlay.x))

    @staticmethod
    def layer_worker_count(
        render_settings: RenderSettings, stream_count: int,
    ) -> int:
        """Bound Canvas-layer encoders according to the selected work mode."""
        if stream_count <= 0:
            return 0
        mode = render_settings.work_mode
        if mode == WORK_MODE_STABLE:
            return 1
        if mode == WORK_MODE_MAX_SPEED:
            pixels = render_settings.output_width * render_settings.output_height
            cap = 2 if pixels >= 3840 * 2160 else 3
            return min(stream_count, cap)
        return min(stream_count, 2)

    @staticmethod
    def png_queue_capacity(render_settings: RenderSettings) -> int:
        """Bound staged 4K QImages according to the selected work mode."""
        if render_settings.work_mode == WORK_MODE_STABLE:
            return 1
        if render_settings.work_mode == WORK_MODE_MAX_SPEED:
            pixels = render_settings.output_width * render_settings.output_height
            return 2 if pixels >= 3840 * 2160 else 5
        return 3

    def video_clips(
        self, tracks: list, playlist_duration: float,
        work_mode: str = WORK_MODE_AUTO, render_scale: float = 1.0,
    ) -> list[VideoClipOverlay]:
        """Expand visible video elements into deterministic FFmpeg clip intervals.

        ``render_scale`` (>= 1.0) lifts clip geometry onto the export's final
        pixel grid to stay aligned with the up-rendered base Canvas stream.
        """
        window = self.window
        clips: list[VideoClipOverlay] = []
        duration_cache: dict[str, float] = {}

        track_by_id = {track.id: track for track in tracks}
        track_windows = [
            (track_by_id[presentation_window.track_id], presentation_window.timeline_start)
            for presentation_window in compile_playlist(tracks).presentation.windows
        ]

        planned_sources: list[tuple[Source, list[tuple[list[str], float, float]]]] = []
        ordered_paths: list[str] = []
        seen_paths: set[str] = set()
        for source in window.store.sources():
            if source.source_type is not SourceType.VIDEO or not source.visible:
                continue
            raw_schedules: list[tuple[list[str], float, float]] = []
            if source.video_timing_mode == "track":
                raw_schedules.extend(
                    (list(track.video_paths), start, track.duration_seconds)
                    for track, start in track_windows if track.video_paths
                )
            else:
                start = min(playlist_duration, max(0.0, source.timeline_start))
                available = (
                    min(source.timeline_duration, playlist_duration - start)
                    if source.timeline_duration > 0.0 else playlist_duration - start
                )
                raw_schedules.append((list(source.video_paths), start, available))
            schedules: list[tuple[list[str], float, float]] = []
            for paths, start, available in raw_schedules:
                visibility_start = max(start, source.timeline_start)
                visibility_end = start + available
                if source.timeline_duration > 0.0:
                    visibility_end = min(
                        visibility_end,
                        source.timeline_start + source.timeline_duration,
                    )
                start = visibility_start
                available = max(0.0, visibility_end - visibility_start)
                if available <= 0.0 or not paths:
                    continue
                schedules.append((paths, start, available))
                for path in paths:
                    if path not in seen_paths:
                        seen_paths.add(path)
                        ordered_paths.append(path)
            if schedules:
                planned_sources.append((source, schedules))

        media_paths: list[Path] = []
        for path in ordered_paths:
            media_path = Path(path)
            if not media_path.is_file():
                raise RenderError(f"Video file is missing: {path}")
            media_paths.append(media_path)
        # FFprobe startup dominates projects with several per-track videos.
        # Probe independent files concurrently, while keeping the small global
        # cap used by the rest of the export pipeline.
        if work_mode == WORK_MODE_STABLE:
            worker_count = min(1, len(media_paths))
        elif work_mode == WORK_MODE_MAX_SPEED:
            worker_count = min(5, len(media_paths))
        else:
            worker_count = min(3, len(media_paths))
        if worker_count:
            with ThreadPoolExecutor(
                max_workers=worker_count, thread_name_prefix="export-video-probe",
            ) as executor:
                probed = list(executor.map(PlaylistService._probe_duration, media_paths))
            for path, probed_duration in zip(ordered_paths, probed, strict=True):
                if probed_duration <= 0.0:
                    raise RenderError(f"Video duration could not be determined: {path}")
                duration_cache[path] = probed_duration

        for source, schedules in planned_sources:
            for paths, start, available in schedules:
                durations = {path: duration_cache[path] for path in paths}
                for occurrence in build_video_occurrences(
                    source, paths, durations, start, available,
                ):
                    overlay_width = max(8, round(source.width * source.scale))
                    overlay_height = max(8, round(source.height * source.scale))
                    # ``round(int * 1.0) == int`` keeps this identical when the
                    # export matches the canvas resolution.
                    clip_x = round(source.x + (source.width - overlay_width) / 2.0)
                    clip_y = round(source.y + (source.height - overlay_height) / 2.0)
                    clips.append(VideoClipOverlay(
                        path=Path(occurrence.path),
                        timeline_start=occurrence.timeline_start,
                        duration_seconds=occurrence.duration_seconds,
                        media_start_seconds=occurrence.media_start_seconds,
                        x=round(clip_x * render_scale),
                        y=round(clip_y * render_scale),
                        width=max(8, round(overlay_width * render_scale)),
                        height=max(8, round(overlay_height * render_scale)),
                        z_index=source.z_index,
                        rotation=source.rotation,
                        opacity=source.opacity,
                        fit_mode=source.image_fit_mode,
                        fill_color=source.fill_color,
                        border_radius=source.border_radius * source.scale * render_scale,
                        brightness=source.brightness,
                        contrast=source.contrast,
                        saturation=source.video_saturation,
                        grayscale=source.video_grayscale,
                        blur=source.blur * render_scale,
                        speed=source.video_speed,
                        loop_input=occurrence.loop_media,
                    ))
        return sorted(clips, key=lambda clip: (clip.z_index, clip.timeline_start))

    # -- completion handlers -----------------------------------------------

    def succeeded(self, result: RenderResult) -> None:
        """Present a completed background export."""
        window = self.window
        if window._export_dialog:
            window._export_dialog.complete(True)
            window._export_dialog = None
        korean = window.translator.language is Language.KOREAN
        message = f"영상 생성 완료: {result.output_path}" if korean else f"Video created: {result.output_path}"
        window.statusBar().showMessage(message, 7000)
        window._active_export_output_path = result.output_path
        window._pending_export_result = result
        window._notify_export_stage("Complete")

    def failed(self, message: str) -> None:
        """Show FFmpeg failure details reported by the worker thread."""
        window = self.window
        LOGGER.error("Video export failed: %s", message)
        if window._export_dialog:
            window._export_dialog.complete(False)
            window._export_dialog = None
        korean = window.translator.language is Language.KOREAN
        window._pending_export_result = None
        window._notify_export_problem(message)
        QMessageBox.critical(window, "내보내기 오류" if korean else "Export error", message)
        window.statusBar().showMessage(message, 7000)

    def cancelled(self) -> None:
        """Close the progress window after safe cancellation and temp cleanup."""
        window = self.window
        if window._export_dialog:
            window._export_dialog.complete(False)
            window._export_dialog = None
        message = "내보내기를 취소했습니다." if window.translator.language is Language.KOREAN else "Export cancelled."
        window._pending_export_result = None
        window._notify_export_problem(message, cancelled=True)
        window.statusBar().showMessage(message, 5000)

    def finished(self) -> None:
        """Release the UI export lock after any worker completion path."""
        window = self.window
        completed_result = window._pending_export_result
        window._pending_export_result = None
        window.activity_progress.finish("export")
        window._unlock_main_form_after_export()
        window._clear_export_frame_staging()
        QTimer.singleShot(0, window._release_render_worker)
        window._resume_close_after_export_cancel()
        if completed_result is not None:
            QTimer.singleShot(
                0,
                lambda result=completed_result: window._show_export_complete_dialog(
                    result,
                ),
            )
        else:
            window._active_export_output_path = None

    def show_complete_dialog(self, result: RenderResult) -> None:
        """Offer useful next actions only after the editor has been unlocked."""
        window = self.window
        dialog = ExportCompleteDialog(
            result.output_path, window.translator, window, validation=result.validation,
        )
        dialog.exec()
        export_again = dialog.export_again_requested
        window._active_export_output_path = None
        if export_again:
            QTimer.singleShot(0, window._export_video)

    def resume_close_after_cancel(self) -> None:
        """Continue a window-close request only after export resources stop."""
        window = self.window
        if not window._close_after_export_cancel:
            return
        window._close_after_export_cancel = False
        QTimer.singleShot(0, window.close)

    def release_render_worker(self) -> None:
        """Delete the completed worker after its queued result signal is delivered."""
        window = self.window
        if window._render_worker:
            window._render_worker.deleteLater()
        window._render_worker = None
