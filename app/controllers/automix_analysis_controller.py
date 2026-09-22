"""Background AutoMix analysis for the currently loaded playlist.

Runs off the GUI thread (roadmap Phase 1 section 7 / Phase 6 section 4:
"Do not freeze the UI"). Results here are session-only, in-memory display
data for MainWindow's playlist badges and track-details panel -- the
durable, cross-session cache is AnalysisCache (app/automix/cache.py);
this controller only orchestrates one background pass over the current
playlist and reports what came back.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from app.automix.analysis.basic import BasicAnalysisProvider
from app.automix.models import TrackAnalysis
from app.automix.workflow import AutoMixWorkflow
from app.models.playlist import PlaylistTrack


class _AutoMixAnalysisWorker(QThread):
    analyzed = Signal(dict)
    """Emits track_id -> TrackAnalysis for every track that analyzed successfully."""

    def __init__(
        self, tracks: list[PlaylistTrack], ffmpeg_executable: Path, parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._workflow = AutoMixWorkflow(BasicAnalysisProvider(ffmpeg_executable))
        self._tracks = tracks
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        result = self._workflow.analyze(self._tracks, cancel_event=self._cancel_event)
        if not self._cancel_event.is_set():
            self.analyzed.emit(result.analyses)


class AutoMixAnalysisController(QObject):
    """Owns at most one AutoMix analysis pass at a time."""

    analyses_updated = Signal(dict)
    """track_id -> TrackAnalysis for the tracks that just finished analyzing."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: _AutoMixAnalysisWorker | None = None

    def start(self, tracks: list[PlaylistTrack], ffmpeg_executable: Path) -> None:
        """Analyze ``tracks`` in the background, replacing any run already underway."""
        self.cancel()
        if not tracks:
            return
        worker = _AutoMixAnalysisWorker(tracks, ffmpeg_executable, self)
        worker.analyzed.connect(self.analyses_updated)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def cancel(self) -> None:
        """Stop the in-flight analysis, if any, and wait for its thread to exit."""
        worker, self._worker = self._worker, None
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(5000)
