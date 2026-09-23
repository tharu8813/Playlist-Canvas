"""Progressive AutoMix preview: analysis per track, partial mixes, then the export mix.

Drop-in for PreviewAudioController in AutoMix mode (same ``audio_ready`` /
``audio_failed`` / ``progress`` signals and ``start``/``shutdown``), plus
``progressive_ready`` for partial mixes and ``report_playhead`` so it can
render what the listener needs next. Policy lives in
``app.automix.progressive``; this module only moves work between threads.

``audio_ready`` is still the unmodified export pipeline
(``FFmpegRenderer.prepare_playlist_audio``), run once analysis is complete,
so the final Preview plan is exactly the Export plan.

Every worker result carries the generation it was started for; ``cancel()``
/``shutdown()`` bump the generation, so nothing from a cancelled run can
reach a newer one.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QThread, QTimer, Signal

from app.automix.progressive import (
    Action,
    ProgressiveAnalysis,
    RenderScheduler,
    partial_plan,
    preview_gain,
    render_prefix,
)
from app.automix.settings import AutoMixTransitionSettings
from app.controllers.preview_audio_controller import PreviewAudioController
from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import FFmpegRenderer
from app.utils.subprocess_utils import hidden_process_kwargs

LOGGER = logging.getLogger(__name__)

_LUFS = re.compile(r"I:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*LUFS")
_PEAK = re.compile(r"Peak:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dBFS")


def measure_loudness(executable: Path, path: str, cancel_event: threading.Event) -> tuple[float, float]:
    """(integrated LUFS, sample peak dBFS) of one file via FFmpeg ``ebur128``; -inf when unknown."""
    if cancel_event.is_set():
        return -math.inf, -math.inf
    try:
        with subprocess.Popen(
            [str(executable), "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
             "-af", "ebur128=peak=sample", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            **hidden_process_kwargs(),
        ) as process:
            deadline = monotonic() + 300
            try:
                while True:
                    if cancel_event.is_set():
                        return -math.inf, -math.inf
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, 300)
                    try:
                        _stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
            if process.returncode != 0:
                return -math.inf, -math.inf
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.warning("Preview loudness measurement failed for %s: %s", path, error)
        return -math.inf, -math.inf
    lufs, peaks = _LUFS.findall(stderr), _PEAK.findall(stderr)
    return (float(lufs[-1]) if lufs else -math.inf), (float(peaks[-1]) if peaks else -math.inf)


class _AnalysisWorker(QThread):
    """Rhythm and structure analysis side by side, reporting each track as it lands."""

    rhythm_done = Signal(int, str, object)
    structure_done = Signal(int, str, object)

    def __init__(self, executable: Path, tracks: list[PlaylistTrack], generation: int,
                 cancel_event: threading.Event, structure_enabled: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executable = executable
        self._tracks = tracks
        self._generation = generation
        self._cancel_event = cancel_event
        self._structure_enabled = structure_enabled

    def run(self) -> None:
        structure_thread = None
        if self._structure_enabled:
            structure_thread = threading.Thread(target=self._run_structure, name="progressive-structure")
            structure_thread.start()
        try:
            # Same provider policy and service as prepare_playlist_audio, so the
            # cache entries written here are the ones the final render reads.
            from app.automix.analysis.registry import create_analysis_provider
            from app.automix.analysis.service import AnalysisService

            AnalysisService(create_analysis_provider("auto", self._executable)).analyze_tracks(
                self._tracks, cancel_event=self._cancel_event,
                on_result=lambda track_id, analysis: self.rhythm_done.emit(self._generation, track_id, analysis),
            )
        except Exception as error:  # noqa: BLE001 - degrade to the final render, never crash Preview
            LOGGER.warning("Progressive AutoMix rhythm analysis failed: %s", error)
        finally:
            if structure_thread is not None:
                structure_thread.join()

    def _run_structure(self) -> None:
        try:
            from app.automix.structure.service import StructureAnalysisService
            from app.automix.structure.sonara import SonaraStructureProvider

            StructureAnalysisService(SonaraStructureProvider()).analyze_tracks(
                self._tracks, cancel_event=self._cancel_event,
                on_result=lambda track_id, structure: self.structure_done.emit(self._generation, track_id, structure),
            )
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("Progressive AutoMix structure analysis failed: %s", error)
            for track in self._tracks:
                self.structure_done.emit(self._generation, track.id, None)


class _PartialRenderWorker(QThread):
    """Renders one partial mix (first ``track_count`` clips) to a playable FLAC."""

    ready = Signal(int, str, object, float, float)
    """generation, path, plan, covered_until seconds, linear gain."""
    failed = Signal(int, str)

    def __init__(self, executable: Path, plan, track_count: int, tracks: list[PlaylistTrack],
                 directory: Path, generation: int, cancel_event: threading.Event,
                 gain: float | None, loudness: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executable = executable
        self._plan = plan
        self._track_count = track_count
        self._tracks = tracks
        self._directory = directory
        self._generation = generation
        self._cancel_event = cancel_event
        self._gain = gain
        self._loudness = loudness

    def run(self) -> None:
        from app.automix.renderer import AutoMixAudioPipeline, AutoMixRenderError

        try:
            gain = self._gain
            if gain is None:
                prefix = self._tracks[:self._track_count]
                for track in prefix:
                    if track.id not in self._loudness:
                        self._loudness[track.id] = measure_loudness(self._executable, track.file_path, self._cancel_event)
                gain = preview_gain(
                    {t.id: self._loudness[t.id] for t in prefix},
                    {t.id: t.duration_seconds for t in prefix},
                )
            render, covered_until = render_prefix(self._plan, self._track_count, gain)
            prepared = AutoMixAudioPipeline(self._executable).render(
                render, {track.id: track.file_path for track in self._tracks}, self._directory,
                cancel_event=self._cancel_event, container="flac",
            )
        except AutoMixRenderError as error:
            if not self._cancel_event.is_set():
                self.failed.emit(self._generation, str(error))
            return
        except Exception as error:  # noqa: BLE001
            self.failed.emit(self._generation, str(error))
            return
        if not self._cancel_event.is_set():
            self.ready.emit(self._generation, str(prepared.path), self._plan, covered_until, gain)


def _stop_thread(worker: QThread | None) -> None:
    """Wait out a cancelled worker without re-entering user input, then delete it now.

    Same sequence (and for the same Windows fail-fast reasons) as
    PreviewAudioController.shutdown().
    """
    if worker is None:
        return
    try:
        running = worker.isRunning()
    except RuntimeError:
        return
    if running:
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        if worker.isRunning():
            loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    try:
        worker.wait()
    except RuntimeError:
        return
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)


class ProgressiveAutoMixController(QObject):
    """One progressive AutoMix preview run at a time (see module docstring)."""

    audio_ready = Signal(str, object)
    """Final export-pipeline mix: (path, CompiledRenderPlan)."""
    audio_failed = Signal(str)
    progress = Signal(str, float, str)
    progressive_ready = Signal(str, object, float, float)
    """Partial mix: (path, provisional plan, covered-until seconds, linear gain)."""

    def __init__(self, renderer: FFmpegRenderer, parent: QObject | None = None, *, korean: bool = True) -> None:
        super().__init__(parent)
        self._renderer = renderer
        self._korean = korean
        self._generation = 0
        self._cancel_event = threading.Event()
        self._analysis_worker: _AnalysisWorker | None = None
        self._render_worker: _PartialRenderWorker | None = None
        self._final: PreviewAudioController | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._evaluate)
        self._loudness: dict[str, tuple[float, float]] = {}
        self._gain: float | None = None
        self._progressive_enabled = True
        self._shutting_down = False
        self.render_count = 0
        """Partial renders started (diagnostics/tests)."""

    # -- public API (PreviewAudioController-compatible) ----------------------

    def start(self, tracks: list[PlaylistTrack], output_directory: Path,
              transition_mode: str, crossfade_seconds: float,
              automix_settings: AutoMixTransitionSettings | None = None) -> None:
        """A new generation: any running one (e.g. another preset's) is cancelled first."""
        if self._shutting_down or not tracks:
            return
        self.cancel()
        from app.automix.structure.sonara import sonara_available

        self._generation += 1
        self._cancel_event = threading.Event()
        self._tracks = list(tracks)
        self._directory = Path(output_directory)
        self._crossfade_seconds = crossfade_seconds
        # The same resolved preset plans the partial mixes and the final
        # (export-pipeline) mix, so the final Preview plan is the Export plan.
        self._settings = automix_settings or AutoMixTransitionSettings(enabled=True)
        self._state = ProgressiveAnalysis(self._tracks, structure_enabled=sonara_available())
        self._scheduler = RenderScheduler()
        self._frontier = -1
        self._report_analysis()
        worker = _AnalysisWorker(
            self._renderer.executable, self._tracks, self._generation, self._cancel_event,
            self._state.structure_enabled, self,
        )
        worker.rhythm_done.connect(self._on_rhythm)
        worker.structure_done.connect(self._on_structure)
        worker.finished.connect(lambda worker=worker: self._on_analysis_finished(worker))
        self._analysis_worker = worker
        worker.start()

    def report_playhead(self, seconds: float, playing: bool) -> None:
        """Preview's global playhead; the first report marks Preview as attached."""
        if self._shutting_down or not hasattr(self, "_scheduler"):
            return
        self._scheduler.playhead_changed(seconds, playing)
        self._evaluate()

    def cancel(self) -> None:
        self._generation += 1
        self._cancel_event.set()
        self._timer.stop()
        if self._final is not None:
            self._final.cancel()

    def shutdown(self) -> None:
        self._shutting_down = True
        self.cancel()
        workers = (self._analysis_worker, self._render_worker)
        self._analysis_worker = self._render_worker = None
        for worker in workers:
            _stop_thread(worker)
        if self._final is not None:
            self._final.shutdown()

    # -- analysis ------------------------------------------------------------

    def _on_rhythm(self, generation: int, track_id: str, analysis) -> None:
        if generation == self._generation:
            self._state.record_rhythm(track_id, analysis)
            self._on_analysis_progress()

    def _on_structure(self, generation: int, track_id: str, structure) -> None:
        if generation == self._generation:
            self._state.record_structure(track_id, structure)
            self._on_analysis_progress()

    def _on_analysis_progress(self) -> None:
        frontier = self._state.frontier()
        if frontier != self._frontier:
            # Planning is immediate on every frontier move; rendering is not.
            self._frontier = frontier
            self._plan = partial_plan(self._tracks, self._state, self._settings)
            self._scheduler.plan_updated(self._plan, frontier, monotonic())
        if self._state.complete():
            self._scheduler.analysis_complete = True
        self._report_analysis()
        self._evaluate()

    def _on_analysis_finished(self, worker: _AnalysisWorker) -> None:
        if not self._release(worker, "_analysis_worker") or worker._generation != self._generation:
            return
        # Missing results (failed/cancelled tracks) must not stall the final mix.
        self._scheduler.analysis_complete = True
        self._evaluate()

    # -- rendering -----------------------------------------------------------

    def _evaluate(self) -> None:
        if self._shutting_down or not hasattr(self, "_scheduler"):
            return
        now = monotonic()
        action = self._scheduler.decide(now)
        if action is Action.RENDER and self._progressive_enabled:
            self._start_partial_render()
        elif action is Action.FINAL:
            self._start_final()
        else:
            wait = self._scheduler.wake_after(now)
            if wait is not None:
                self._timer.start(max(10, round(wait * 1000)))

    def _start_partial_render(self) -> None:
        self._scheduler.render_started()
        self.render_count += 1
        self._emit(self._message("미리보기 믹스 준비 중…", "Preparing preview…"))
        worker = _PartialRenderWorker(
            self._renderer.executable, self._plan, self._frontier, self._tracks,
            self._directory / f"partial-{self.render_count}", self._generation, self._cancel_event,
            self._gain, self._loudness, self,
        )
        worker.ready.connect(self._on_partial_ready)
        worker.failed.connect(self._on_partial_failed)
        worker.finished.connect(lambda worker=worker: self._release(worker, "_render_worker"))
        self._render_worker = worker
        worker.start()

    def _release(self, worker: QThread, attribute: str) -> bool:
        """Forget a finished worker; False while shutdown() owns its deletion."""
        if self._shutting_down:
            return False
        if getattr(self, attribute) is worker:
            setattr(self, attribute, None)
        worker.deleteLater()
        return True

    def _on_partial_ready(self, generation: int, path: str, plan, covered_until: float, gain: float) -> None:
        if generation != self._generation:
            return
        self._scheduler.render_finished()
        self._gain = gain  # frozen: every partial mix shares one level
        self.progressive_ready.emit(path, plan, covered_until, gain)
        self._emit(self._message(
            f"AutoMix 적용: {self._scheduler.rendered_frontier}번째 곡까지",
            f"AutoMix ready through track {self._scheduler.rendered_frontier}",
        ))
        self._evaluate()

    def _on_partial_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        LOGGER.warning("Progressive AutoMix partial render failed; waiting for the final mix: %s", message)
        self._scheduler.render_finished()
        self._progressive_enabled = False
        self._evaluate()

    def _start_final(self) -> None:
        self._scheduler.render_started(final=True)
        self._emit(self._message("AutoMix 마무리 중…", "Finalizing AutoMix…"))
        final = PreviewAudioController(self._renderer, self)
        final.audio_ready.connect(self._on_final_ready)
        final.audio_failed.connect(self.audio_failed.emit)
        final.progress.connect(lambda _stage, fraction, message: self.progress.emit(
            "Finalizing AutoMix", 0.8 + 0.2 * max(0.0, min(1.0, fraction)),
            self._message("AutoMix 마무리 중… ", "Finalizing AutoMix… ") + (message or ""),
        ))
        self._final = final
        final.start(self._tracks, self._directory / "final", "automix", self._crossfade_seconds,
                    automix_settings=self._settings)

    def _on_final_ready(self, path: str, plan) -> None:
        self._scheduler.render_finished()
        self.progress.emit("AutoMix ready", 1.0, self._message("AutoMix 준비 완료", "AutoMix ready"))
        self.audio_ready.emit(path, plan)

    # -- progress text (always from real state) ------------------------------

    def _report_analysis(self) -> None:
        total = len(self._tracks)
        done = self._state.completed_count()
        planned = len(getattr(self, "_plan", None).audio.transitions) if getattr(self, "_plan", None) else 0
        self.progress.emit(
            "Analyzing", 0.8 * done / max(1, total),
            self._message(
                f"곡 분석 {done} / {total} · 전환 계획 {planned} / {max(0, total - 1)}",
                f"Analyzing {done} / {total} · Planning transitions {planned} / {max(0, total - 1)}",
            ),
        )

    def _emit(self, message: str) -> None:
        total = len(self._tracks)
        self.progress.emit("AutoMix", 0.8 * self._state.completed_count() / max(1, total), message)

    def _message(self, korean: str, english: str) -> str:
        return korean if self._korean else english
