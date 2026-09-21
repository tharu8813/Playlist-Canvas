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

The core `_export_video` render orchestration and the FFmpeg
install/catalog methods are deliberately left in MainWindow for a later
phase: they are larger, touch live encoder hardware, and carry the
highest regression risk in the codebase (pixel/timing-sensitive capture
caching and FFmpeg process lifecycle).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageWriter
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

from app.dialogs.export_progress_dialog import ExportProgressDialog
from app.renderer.ffmpeg_renderer import RenderCancelledError, RenderError, RenderFrame
from app.renderer.png_frame_staging import (
    PngFrameStagingCancelled,
    PngFrameStagingError,
    PngFrameStagingPipeline,
)
from app.services.app_settings_service import AppSettings
from app.services.export_storage_service import ExportStorageMonitor
from app.utils.i18n import Language

if TYPE_CHECKING:
    import threading

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
