"""Capture every planned Canvas state and encode the export's visual streams."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from heapq import heappop, heappush
from pathlib import Path
from time import monotonic
import threading

from PySide6.QtGui import QImage

from app.models.playlist import PlaylistTrack
from app.preview.export_canvas_capture import ExportCanvasCapturer
from app.preview.export_plan import ExportPlan
from app.renderer.export_timeline import ExportFrameSample
from app.renderer.ffmpeg_renderer import (
    PreparedStaticOverlayLayer,
    PreparedVideoInput,
    RenderCancelledError,
    RenderError,
    RenderFrame,
    RenderSettings,
    StaticOverlayLayer,
)
from app.renderer.static_video_stream import (
    StaticVideoStreamEncoder,
    StaticVideoStreamError,
)

LOGGER = logging.getLogger(__name__)

# Fraction of the export progress bar reserved for Canvas preparation; the
# remainder belongs to FFmpeg encoding. Mirrors the main window constant.
PREPARATION_PROGRESS_WEIGHT = 0.25


@dataclass(frozen=True, slots=True)
class ExportArtifacts:
    """The prepared visual inputs handed to :class:`FFmpegRenderer`."""

    frames: list[RenderFrame] | PreparedVideoInput
    static_layers: list


@dataclass(frozen=True, slots=True)
class PngStaging:
    """Window-owned disk-backed PNG frame staging, injected for testability.

    ``start_pipeline`` takes the queue capacity; the others take nothing.
    """

    stage_frame: Callable[[QImage, float, str], RenderFrame]
    start_pipeline: Callable[[int], None]
    finish_pipeline: Callable[[], None]
    cancel_pipeline: Callable[[], None]
    pending_frames: Callable[[], int]
    queue_capacity: int


class ExportSession:
    """Run the Canvas capture loop and lossless stream encoding for one export.

    Everything that touches the temporary directory, the progress dialog, or the
    main window's PNG staging is injected. The session produces exactly the two
    things the renderer needs: the base ``frames`` and any ``static_layers``.
    """

    def __init__(
        self,
        *,
        scene: object,
        renderer: object,
        plan: ExportPlan,
        render_settings: RenderSettings,
        active_tracks: Sequence[PlaylistTrack],
        stream_root: Path | None,
        preparation_cancel: threading.Event,
        korean: bool,
        staging: PngStaging,
        layer_worker_count: Callable[[int], int],
        report_progress: Callable[[float, str], None],
        pump_ui: Callable[[], None],
    ) -> None:
        self._scene = scene
        self._renderer = renderer
        self._plan = plan
        self._settings = render_settings
        self._tracks = list(active_tracks)
        self._stream_root = stream_root
        self._cancel = preparation_cancel
        self._korean = korean
        self._staging = staging
        self._layer_worker_count = layer_worker_count
        self._report_progress = report_progress
        self._pump_ui = pump_ui

        self._active_encoders: dict[str, StaticVideoStreamEncoder] = {}
        self._sparse_stream_frames: dict[str, list[RenderFrame]] = {}
        self._capture_count = 0
        self._total_captures = 1
        self._invariant_stream_keys: set[str] = set()
        self._sparse_invariant_stream_keys: set[str] = set()
        self._stream_wait_last_update: dict[str, float] = {}
        self._capturer: ExportCanvasCapturer | None = None

    @property
    def capture_count(self) -> int:
        return self._capture_count

    def cancel_streams(self) -> None:
        """Stop every active encoder and the PNG pipeline (cleanup on failure)."""
        for encoder in tuple(self._active_encoders.values()):
            encoder.cancel()
        self._active_encoders.clear()
        self._staging.cancel_pipeline()

    # -- capture loop -----------------------------------------------------

    def run(self) -> ExportArtifacts:
        plan = self._plan
        z_bands = plan.z_bands
        stream_timeline_samples = plan.stream_timeline_samples
        playlist_duration = plan.playlist_duration

        stream_specs = self._build_stream_specs()
        independent_capture_count = sum(
            len(samples) for samples in stream_timeline_samples.values()
        )
        self._total_captures = max(1, independent_capture_count)

        capturer = ExportCanvasCapturer(
            self._scene,
            self._tracks,
            playlist_duration,
            plan.dynamic_visualizer_ids,
            z_bands,
            (
                self._stage_frame
                if plan.use_streamed_visuals
                else self._staging.stage_frame
            ),
            self._ensure_not_cancelled,
            self._after_capture,
            retain_static_frames=not plan.use_streamed_visuals,
            output_scale=plan.canvas_render_scale,
        )
        self._capturer = capturer
        self._invariant_stream_keys = capturer.invariant_stream_keys
        self._sparse_invariant_stream_keys = (
            set(self._invariant_stream_keys)
            if not plan.direct_final_stream else set()
        )
        if plan.use_streamed_visuals and self._sparse_invariant_stream_keys:
            self._staging.start_pipeline(self._staging.queue_capacity)
            stream_specs = [
                spec for spec in stream_specs
                if spec[0] not in self._sparse_invariant_stream_keys
            ]

        stream_keys = [
            "base", *(f"layer:{index}" for index in range(len(z_bands) - 1)),
        ]
        state_key_skipped_captures = 0
        for stream_key in stream_keys:
            if stream_key in self._invariant_stream_keys:
                continue
            original_samples = stream_timeline_samples[stream_key]
            coalesced_samples = capturer.coalesce_samples(
                original_samples, stream_key,
            )
            state_key_skipped_captures += (
                len(original_samples) - len(coalesced_samples)
            )
            stream_timeline_samples[stream_key] = coalesced_samples
        self._total_captures = max(
            1,
            sum(
                1 if stream_key in self._invariant_stream_keys
                else len(stream_timeline_samples[stream_key])
                for stream_key in stream_keys
            ),
        )
        LOGGER.info(
            "Canvas independent timelines: band_captures=%d "
            "optimized_captures=%d state_key_skipped=%d per_stream=%s",
            independent_capture_count,
            self._total_captures,
            state_key_skipped_captures,
            ",".join(
                f"{key}:{len(stream_timeline_samples[key])}"
                for key in stream_keys
            ),
        )

        if plan.use_streamed_visuals:
            artifacts = self._run_streamed(stream_specs, stream_keys)
        else:
            artifacts = self._run_png(stream_keys)

        self._finalize_progress(len(stream_keys))
        self._log_partial_render_metrics()
        if self._cancel.is_set():
            raise RenderCancelledError("Export preparation was cancelled.")
        return artifacts

    # -- streamed lossless path ----------------------------------------

    def _run_streamed(
        self, stream_specs: list, stream_keys: list[str],
    ) -> ExportArtifacts:
        plan = self._plan
        settings = self._settings
        playlist_duration = plan.playlist_duration
        stream_timeline_samples = plan.stream_timeline_samples
        duration_tolerance = 1e-6 * max(1.0, playlist_duration)
        streamed_results: dict[str, object] = {}

        layer_parallelism = self._layer_worker_count(len(stream_specs))
        LOGGER.info(
            "Canvas layer preparation: streams=%d parallel_encoders=%d "
            "sparse_invariant=%d",
            len(stream_specs), layer_parallelism,
            len(self._sparse_invariant_stream_keys),
        )
        for stream_key in stream_keys:
            if stream_key in self._sparse_invariant_stream_keys:
                self._capturer.capture_invariant_stream(
                    stream_timeline_samples[stream_key][0],
                    stream_key,
                    playlist_duration,
                )
        for batch_start in range(0, len(stream_specs), layer_parallelism):
            batch = stream_specs[batch_start:batch_start + layer_parallelism]
            try:
                for stream_key, output_path, preserve_alpha, queue_capacity in batch:
                    self._active_encoders[stream_key] = StaticVideoStreamEncoder(
                        self._renderer.executable,
                        output_path,
                        settings.fps,
                        queue_capacity=queue_capacity,
                        preserve_alpha=preserve_alpha,
                        producer_cancel_event=self._cancel,
                        producer_wait_callback=(
                            lambda key=stream_key: self._stream_wait(key)
                        ),
                        direct_profile=(
                            self._renderer.direct_encoding_profile(settings)
                            if plan.direct_final_stream and stream_key == "base"
                            else None
                        ),
                    )
                for stream_key, _path, _alpha, _capacity in batch:
                    if stream_key in self._invariant_stream_keys:
                        self._capturer.capture_invariant_stream(
                            stream_timeline_samples[stream_key][0],
                            stream_key,
                            playlist_duration,
                        )
                pending_samples: list[
                    tuple[float, int, int, str, ExportFrameSample]
                ] = []
                batch_keys = [item[0] for item in batch]
                for stream_order, stream_key in enumerate(batch_keys):
                    if stream_key in self._invariant_stream_keys:
                        continue
                    samples = stream_timeline_samples[stream_key]
                    first = samples[0]
                    heappush(pending_samples, (
                        first.timeline_seconds, stream_order, 0, stream_key, first,
                    ))
                while pending_samples:
                    (
                        _timeline_seconds, stream_order, sample_index,
                        stream_key, sample,
                    ) = heappop(pending_samples)
                    self._capturer.capture_stream(sample, stream_key)
                    next_index = sample_index + 1
                    samples = stream_timeline_samples[stream_key]
                    if next_index < len(samples):
                        next_sample = samples[next_index]
                        heappush(pending_samples, (
                            next_sample.timeline_seconds, stream_order,
                            next_index, stream_key, next_sample,
                        ))
                for stream_key, _path, _alpha, _capacity in batch:
                    encoder = self._active_encoders[stream_key]
                    LOGGER.info(
                        "Draining Canvas stream: key=%s written_frames=%d "
                        "expected_frames=%d",
                        stream_key,
                        int(getattr(encoder, "frame_count", 0)),
                        max(1, round(playlist_duration * settings.fps)),
                    )
                    streamed = encoder.finish()
                    self._active_encoders.pop(stream_key, None)
                    if abs(
                        streamed.duration_seconds - playlist_duration
                    ) > duration_tolerance:
                        difference = streamed.duration_seconds - playlist_duration
                        raise RenderError(
                            "A streamed Canvas timeline does not match the playlist "
                            f"duration (expected {playlist_duration:.6f}s, got "
                            f"{streamed.duration_seconds:.6f}s, difference "
                            f"{difference:+.6f}s)."
                        )
                    expected_alpha = stream_key != "base"
                    if streamed.has_alpha_stream != expected_alpha:
                        raise RenderError(
                            "A streamed Canvas layer has an invalid alpha configuration."
                        )
                    streamed_results[stream_key] = streamed
            except StaticVideoStreamError as error:
                self.cancel_streams()
                if self._cancel.is_set():
                    raise RenderCancelledError(
                        "Export preparation was cancelled."
                    ) from error
                raise RenderError(str(error)) from error
            except Exception:
                self.cancel_streams()
                raise

        if self._sparse_invariant_stream_keys:
            self._staging.finish_pipeline()
        if "base" in self._sparse_invariant_stream_keys:
            frames: list[RenderFrame] | PreparedVideoInput = (
                self._sparse_stream_frames["base"]
            )
            LOGGER.info(
                "Sparse invariant Canvas base: images=%d duration=%.3fs",
                len(frames), playlist_duration,
            )
        else:
            streamed = streamed_results["base"]
            frames = PreparedVideoInput(
                streamed.path,
                playlist_duration,
                streamed.width,
                streamed.height,
                streamed.fps,
                ready_for_mux=plan.direct_final_stream,
                encoded_codec=(
                    settings.video_codec if plan.direct_final_stream else ""
                ),
            )
            try:
                stream_bytes = streamed.path.stat().st_size
            except OSError:
                stream_bytes = -1
            LOGGER.info(
                "Streamed Canvas summary: frames=%d duration=%.3fs "
                "resolution=%dx%d queue_peak=%d file_bytes=%d",
                streamed.frame_count, streamed.duration_seconds,
                streamed.width, streamed.height,
                streamed.peak_buffered_frames, stream_bytes,
            )
        static_layers = [
            (
                StaticOverlayLayer(
                    z_min if z_min is not None else -10_000.0,
                    self._sparse_stream_frames[f"layer:{index}"],
                    *self._capturer.stream_origin(f"layer:{index}"),
                )
                if f"layer:{index}" in self._sparse_invariant_stream_keys else
                PreparedStaticOverlayLayer(
                    z_min if z_min is not None else -10_000.0,
                    PreparedVideoInput(
                        streamed_results[f"layer:{index}"].path,
                        playlist_duration,
                        streamed_results[f"layer:{index}"].width,
                        streamed_results[f"layer:{index}"].height,
                        streamed_results[f"layer:{index}"].fps,
                    ),
                    *self._capturer.stream_origin(f"layer:{index}"),
                )
            )
            for index, (z_min, _z_max) in enumerate(plan.z_bands[1:])
        ]
        return ExportArtifacts(frames, static_layers)

    # -- PNG fallback path -------------------------------------------

    def _run_png(self, stream_keys: list[str]) -> ExportArtifacts:
        plan = self._plan
        stream_timeline_samples = plan.stream_timeline_samples
        self._staging.start_pipeline(self._staging.queue_capacity)
        captured_stream_frames: dict[str, list[RenderFrame]] = {}
        for stream_key in stream_keys:
            samples = stream_timeline_samples[stream_key]
            if stream_key in self._invariant_stream_keys:
                captured_stream_frames[stream_key] = [
                    self._capturer.capture_invariant_stream(
                        samples[0], stream_key, plan.playlist_duration,
                    )
                ]
            else:
                captured_stream_frames[stream_key] = [
                    self._capturer.capture_stream(sample, stream_key)
                    for sample in samples
                ]
        frames = captured_stream_frames["base"]
        self._report_progress(
            PREPARATION_PROGRESS_WEIGHT,
            "남은 화면 프레임을 저장하고 있습니다."
            if self._korean else
            "Finishing the remaining background frame writes.",
        )
        self._staging.finish_pipeline()
        return ExportArtifacts(frames, self._capturer.static_layers())

    # -- helpers -----------------------------------------------------

    def _build_stream_specs(self) -> list[tuple[str, Path, bool, int]]:
        if not self._plan.use_streamed_visuals:
            return []
        assert self._stream_root is not None
        root = self._stream_root
        specs: list[tuple[str, Path, bool, int]] = [
            ("base", root / "canvas-base.mkv", False, 3),
        ]
        specs.extend(
            (
                f"layer:{index}",
                root / f"canvas-layer-{index:02d}.mkv",
                True,
                2,
            )
            for index, _band in enumerate(self._plan.z_bands[1:])
        )
        return specs

    def _ensure_not_cancelled(self) -> None:
        if self._cancel.is_set():
            raise RenderCancelledError("Export preparation was cancelled.")

    def _stage_frame(
        self, image: QImage, duration_seconds: float, stream_key: str,
    ) -> RenderFrame:
        # Do not expand one invariant Canvas image into every CFR frame through a
        # CPU-only intermediate encoder. Preserve the single lossless image and
        # duration; the final graph applies the output FPS.
        if (
            self._plan.use_streamed_visuals
            and stream_key in self._sparse_invariant_stream_keys
        ):
            rendered = self._staging.stage_frame(image, duration_seconds, stream_key)
            self._sparse_stream_frames.setdefault(stream_key, []).append(rendered)
            return rendered
        return self._stage_streamed_frame(image, duration_seconds, stream_key)

    def _stage_streamed_frame(
        self, image: QImage, duration_seconds: float, stream_key: str,
    ) -> RenderFrame:
        encoder = self._active_encoders.get(stream_key)
        if encoder is None:
            raise RenderError("Invalid streamed Canvas frame configuration.")
        try:
            encoder.submit(image, duration_seconds)
        except StaticVideoStreamError as error:
            if self._cancel.is_set():
                raise RenderCancelledError(
                    "Export preparation was cancelled."
                ) from error
            raise RenderError(str(error)) from error
        return RenderFrame(encoder.output_path, max(0.001, duration_seconds))

    def _after_capture(self, track_number: int, _stream_key: str) -> None:
        """Capturer callback after each staged Canvas state."""
        self._capture_count += 1
        completed = min(self._capture_count, self._total_captures)
        if completed % 4 != 0 and completed != self._total_captures:
            return
        preparation_fraction = completed / self._total_captures
        overall = preparation_fraction * PREPARATION_PROGRESS_WEIGHT
        percent = round(preparation_fraction * 100)
        position = f"{track_number}/{len(self._tracks)}"
        if self._plan.use_streamed_visuals:
            detail = (
                f"장면을 영상으로 준비하는 중 · {position}번 곡 · "
                f"전체 {percent}% · 장면 "
                f"{completed:,}/{self._total_captures:,}"
                if self._korean else
                f"Preparing scenes for the video · track {position} · "
                f"{percent}% overall · "
                f"{completed:,}/{self._total_captures:,} scenes"
            )
        else:
            pending = self._staging.pending_frames()
            detail = (
                f"장면을 영상으로 준비하는 중 · {position}번 곡 · "
                f"전체 {percent}% · 장면 "
                f"{completed:,}/{self._total_captures:,} · 저장 대기 {pending}개"
                if self._korean else
                f"Preparing scenes for the video · track {position} · "
                f"{percent}% overall · "
                f"{completed:,}/{self._total_captures:,} scenes · "
                f"{pending} waiting to save"
            )
        self._report_progress(overall, detail)
        self._pump_ui()
        if self._cancel.is_set():
            raise RenderCancelledError("Export preparation was cancelled.")

    def _stream_wait(self, stream_key: str) -> None:
        """Encoder producer-wait callback: repaint and show drain progress."""
        self._pump_ui()
        if self._cancel.is_set():
            return
        now = monotonic()
        if now - self._stream_wait_last_update.get(stream_key, 0.0) < 0.15:
            return
        self._stream_wait_last_update[stream_key] = now
        encoder = self._active_encoders.get(stream_key)
        if encoder is None:
            return
        expected = max(1, round(self._plan.playlist_duration * self._settings.fps))
        encoded = min(int(getattr(encoder, "frame_count", 0)), expected)
        percent = round(encoded / expected * 100)
        capture_fraction = min(1.0, self._capture_count / self._total_captures)
        overall = capture_fraction * PREPARATION_PROGRESS_WEIGHT
        detail = (
            "화면 구성 요소를 영상으로 변환하는 중 · "
            f"{encoded:,}/{expected:,} 프레임 · {percent}%"
            if self._korean else
            "Converting visual elements into video · "
            f"{encoded:,}/{expected:,} frames · {percent}%"
        )
        self._report_progress(overall, detail)

    def _finalize_progress(self, stream_count: int) -> None:
        prepared = max(1, stream_count)
        detail = (
            f"화면 스트림 {prepared}/{prepared} · 준비 100%"
            if self._korean else
            f"Visual streams {prepared}/{prepared} · 100% prepared"
        )
        self._report_progress(PREPARATION_PROGRESS_WEIGHT, detail)

    def _log_partial_render_metrics(self) -> None:
        capturer = self._capturer
        if capturer is None:
            return
        avoided = max(
            0,
            capturer.full_frame_source_pixels - capturer.scene_render_source_pixels,
        )
        avoided_percent = avoided / max(1, capturer.full_frame_source_pixels) * 100.0
        LOGGER.info(
            "Canvas partial-region rendering: captures=%d scene_pixels=%d "
            "full_frame_pixels=%d avoided=%.1f%%",
            capturer.partial_render_capture_count,
            capturer.scene_render_source_pixels,
            capturer.full_frame_source_pixels,
            avoided_percent,
        )
