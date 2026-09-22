"""Optional advanced AutoMix analyzer: Beat This! neural beat/downbeat tracking.

Beat This! (CPJKU/beat_this on GitHub and PyPI as ``beat-this``; MIT license
for both code and the pretrained weights -- confirmed from the upstream
README/LICENSE/pyproject.toml at integration time) gives genuinely
model-based downbeats instead of BasicAnalysisProvider's provisional "every
4th beat" 4/4 guess (see app/automix/analysis/basic.py's module docstring).
That provisional guess is exactly what BasicAnalysisProvider deliberately
caps at PROVISIONAL_METER_CONFIDENCE=0.3, below
TrackAnalysis.RELIABLE_METER_CONFIDENCE=0.5 -- so a real downbeat model is
what actually unlocks BEAT_MATCH-quality transitions in
app/automix/candidates.py, not a planner change.

``torch``/``beat_this`` are heavyweight, optional runtime dependencies (not
in requirements.txt -- see docs/automix-phase1-dependency-evaluation.md's
"Beat This!" section for the original size/license evaluation) and are
imported lazily, inside _load_model(), only once analysis is actually
attempted -- never at module import time, matching the same principle
app/controllers/automix_analysis_controller.py already documents for
librosa. A missing/broken install, a failed/corrupted model download, or an
inference error for one track all degrade to BasicAnalysisProvider's own
result for that track: AutoMix, and the app as a whole, must keep working
with this engine completely absent.

This is a hybrid provider (key/energy/vocal-activity/validation/silence
handling are delegated to an owned BasicAnalysisProvider instance, not
reimplemented) -- Beat This! only ever replaces the rhythm fields (bpm,
bpm_confidence, beats, downbeats, meter_*).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

import numpy as np

from app.automix.analysis.basic import BasicAnalysisProvider, normalize_tempo_octave
from app.automix.analysis.provider import AnalysisCancelled
from app.automix.models import TrackAnalysis
from app.models.playlist import PlaylistTrack

LOGGER = logging.getLogger(__name__)

DEFAULT_CHECKPOINT = "final0"
"""Beat This!'s default pretrained checkpoint (~78 MB). Downloaded and
cached by the ``beat_this`` package itself on first use, in its own cache
directory -- unlike app/ffmpeg/managed_installer.py, this provider does not
implement its own download/checksum/staging pipeline for the model, since
one already exists upstream. A corrupted or interrupted download surfaces
as an exception from beat_this's own inference call, which analyze() below
catches like any other inference failure and degrades to the basic
analyzer for that track."""

MINIMUM_BEATS_FOR_TEMPO = 4
"""Below this, an inter-beat-interval statistic is not meaningful (matches
BasicAnalysisProvider._bpm_confidence's own minimum)."""
MINIMUM_DOWNBEATS_FOR_METER = 2
"""At least one full bar interval is needed to say anything about meter regularity."""

_TIMESTAMP_DEDUPE_TOLERANCE_SECONDS = 1e-3


def _installed_beat_this_version() -> str:
    """The installed ``beat-this`` distribution's version, or "unavailable".

    Uses importlib.metadata, which reads installed-package metadata
    (dist-info) without importing the package itself -- safe to call
    unconditionally, even when torch/beat_this are not installed at all,
    without triggering the heavy import this whole module otherwise avoids
    until analyze() actually needs it.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("beat-this")
    except PackageNotFoundError:
        return "unavailable"
    except Exception:  # noqa: BLE001 - a version string must never block construction
        return "unknown"


class BeatThisAnalysisProvider:
    """Hybrid AnalysisProvider: Beat This! for rhythm, BasicAnalysisProvider for the rest.

    One instance loads its model at most once (see _load_model, guarded by
    a lock for AnalysisService's concurrent per-track worker threads) and
    reuses it for every track passed to analyze() -- construct one instance
    per analysis batch/session, never one per track.
    """

    provider_id = "beat_this"
    version = "1"
    """This *implementation's* version: bump it if the confidence
    calibration or output mapping in this module changes in a way that
    should invalidate previously cached results, independent of the
    upstream model/package version. Accessed on the class (not an
    instance) this stays "1" -- see __init__, which combines it with the
    checkpoint name and the installed ``beat-this`` package version into
    the actual per-instance cache identity (self.version), so a checkpoint
    change or a package upgrade also invalidates old cache entries without
    a manual version bump here."""

    def __init__(
        self, ffmpeg_executable: Path, *,
        device: str | None = None, checkpoint: str = DEFAULT_CHECKPOINT,
    ) -> None:
        self._basic = BasicAnalysisProvider(ffmpeg_executable)
        self._requested_device = device
        self._checkpoint = checkpoint
        self._model = None
        self._model_lock = threading.Lock()
        # Shadows the class attribute above with the full cache identity for
        # this instance (app/automix/cache.py keys entries by
        # provider_id+version). importlib.metadata reads installed-package
        # metadata without importing/executing the package, so this never
        # eagerly loads torch just to compute a version string.
        self.version = f"{BeatThisAnalysisProvider.version}+{checkpoint}+pkg{_installed_beat_this_version()}"

    def analyze(
        self, track: PlaylistTrack, *,
        cancel_event: threading.Event,
        progress: Callable[[float, str], None] | None = None,
    ) -> TrackAnalysis:
        # Reuses every bit of BasicAnalysisProvider's decode, silence/
        # too-short handling, key/energy/vocal-activity estimation, and
        # TrackAnalysis validation -- only the rhythm fields below are ever
        # replaced (see module docstring).
        basic_result = self._basic.analyze(track, cancel_event=cancel_event, progress=progress)
        if cancel_event.is_set():
            raise AnalysisCancelled("AutoMix analysis cancelled before Beat This inference.")
        if basic_result.energy is None:
            # `energy` is only ever left at its default None by
            # BasicAnalysisProvider's own early return for a silent/too-short
            # track (see basic.py's MINIMUM_ANALYZABLE_SECONDS/
            # SILENCE_PEAK_THRESHOLD check) -- every other path through
            # analyze() always computes a real energy value, even when
            # librosa's own BPM estimate is rejected as implausible. That
            # distinction matters: "librosa could not find a usable BPM" is
            # not the same thing as "there is no audio signal to analyze",
            # and Beat This! can succeed on tracks librosa's beat tracker
            # fails on (that is the whole point of adding it). Skipping
            # inference here is therefore only a true silent/too-short
            # short-circuit, never a proxy for "basic found no BPM".
            return basic_result
        try:
            raw_beats, raw_downbeats = self._run_inference(track, cancel_event, progress)
        except AnalysisCancelled:
            raise
        except Exception as error:  # noqa: BLE001 - the ML engine must never break AutoMix
            LOGGER.warning(
                "Beat This analysis unavailable for %s (%s); using the basic beat/downbeat estimate instead.",
                track.file_path, error,
            )
            return basic_result
        if cancel_event.is_set():
            raise AnalysisCancelled("AutoMix analysis cancelled after Beat This inference.")

        beats = _sanitize_timestamps(raw_beats, basic_result.duration_seconds)
        if not beats:
            return basic_result
        bpm, bpm_confidence = _bpm_from_beats(np.array(beats))
        if bpm is None:
            return basic_result

        downbeats = _sanitize_timestamps(raw_downbeats, basic_result.duration_seconds)
        if downbeats:
            meter_confidence = _meter_confidence(np.array(beats), np.array(downbeats))
            meter_numerator, meter_denominator = 4, 4
        else:
            # No real downbeats from the model for this track: keep the
            # basic analyzer's own provisional bar guess (already
            # deliberately low-confidence, see PROVISIONAL_METER_CONFIDENCE)
            # rather than reporting an empty meter.
            downbeats = basic_result.downbeats
            meter_confidence = basic_result.meter_confidence
            meter_numerator = basic_result.meter_numerator
            meter_denominator = basic_result.meter_denominator

        return replace(
            basic_result,
            bpm=bpm,
            bpm_confidence=bpm_confidence,
            beats=beats,
            downbeats=downbeats,
            meter_numerator=meter_numerator,
            meter_denominator=meter_denominator,
            meter_confidence=meter_confidence,
            analyzer_id=self.provider_id,
            analyzer_version=self.version,
        )

    def _run_inference(
        self, track: PlaylistTrack, cancel_event: threading.Event,
        progress: Callable[[float, str], None] | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        path = Path(track.file_path)
        # No separate existence check here: BasicAnalysisProvider.analyze()
        # (called first, see analyze() above) already decoded this exact
        # file successfully, and File2Beats raises its own error for a
        # missing/unreadable file otherwise -- caught below like any other
        # inference failure.
        if progress is not None:
            progress(0.92, "Loading Beat This model")
        file2beats = self._load_model()
        if cancel_event.is_set():
            # PyTorch inference itself cannot be interrupted mid-forward-pass;
            # this is the latest point cancellation can still be honored
            # before committing to the (uninterruptible) inference call.
            raise AnalysisCancelled("AutoMix analysis cancelled before Beat This inference.")
        if progress is not None:
            progress(0.95, "Running Beat This inference")
        with self._model_lock:
            # Serialize actual inference: AnalysisService analyzes several
            # tracks concurrently on a thread pool, but running several
            # forward passes through the same model at once wastes CPU/GPU
            # rather than speeding anything up, and risks CUDA OOM on
            # constrained GPUs. Decoding/Basic analysis above stays
            # parallel; only the model call itself is serialized.
            beats, downbeats = file2beats(str(path))
        return np.asarray(beats, dtype=float), np.asarray(downbeats, dtype=float)

    def _load_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:  # re-check: lost a race to load
                return self._model
            import torch
            from beat_this.inference import File2Beats

            device = self._requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
            LOGGER.info("Loading Beat This model %r on %s", self._checkpoint, device)
            self._model = File2Beats(checkpoint_path=self._checkpoint, device=device, dbn=False)
            return self._model


def _sanitize_timestamps(values: np.ndarray, duration_seconds: float) -> tuple[float, ...]:
    """Sort, clip to duration, and drop near-duplicates -- same discipline as
    BasicAnalysisProvider._clean_beats, applied here so a TrackAnalysis built
    from Beat This! output always satisfies TrackAnalysis.__post_init__
    regardless of the model's own timestamp precision/rounding."""
    cleaned: list[float] = []
    previous = float("-inf")
    for value in sorted(float(v) for v in values if np.isfinite(v) and v >= 0.0):
        clipped = min(value, duration_seconds)
        if clipped - previous < _TIMESTAMP_DEDUPE_TOLERANCE_SECONDS:
            continue
        cleaned.append(clipped)
        previous = clipped
    return tuple(cleaned)


def _bpm_from_beats(beats: np.ndarray) -> tuple[float | None, float]:
    """Median inter-beat interval, robust to a handful of misdetected beats.

    Confidence calibration (documented per roadmap "clearly documented"
    requirement):

    - 0.0            = unusable (fewer than MINIMUM_BEATS_FOR_TEMPO beats,
                        or a degenerate/zero interval)
    - ~0.35 and up    = minimum usable (TrackAnalysis.INSUFFICIENT_BPM_CONFIDENCE)
    - ~0.6 and up     = reliable (TrackAnalysis.RELIABLE_BPM_CONFIDENCE)
    - 1.0             = a near-metronomic beat grid

    Built from the median absolute deviation of inter-beat intervals around
    their median (less outlier-sensitive than BasicAnalysisProvider's own
    mean/stddev coefficient-of-variation, appropriate here since a handful
    of misdetected beats should not tank confidence for an otherwise steady
    track): ``confidence = clamp(1 - 3 * (MAD / median_interval), 0, 1)``.
    A track with a genuinely steady beat -- exactly the case Beat This! is
    meant to unlock over the basic analyzer's provisional guess -- lands
    close to 1.0.
    """
    if len(beats) < MINIMUM_BEATS_FOR_TEMPO:
        return None, 0.0
    intervals = np.diff(np.sort(beats))
    intervals = intervals[intervals > 0.0]
    if len(intervals) < MINIMUM_BEATS_FOR_TEMPO - 1:
        return None, 0.0
    median_interval = float(np.median(intervals))
    if median_interval <= 0.0:
        return None, 0.0
    bpm = normalize_tempo_octave(60.0 / median_interval)
    deviation = float(np.median(np.abs(intervals - median_interval)))
    confidence = max(0.0, min(1.0, 1.0 - (deviation / median_interval) * 3.0))
    return bpm, confidence


def _meter_confidence(beats: np.ndarray, downbeats: np.ndarray) -> float:
    """How consistent the downbeat-to-downbeat (bar) interval is, weighted by coverage.

    Same calibration spirit as _bpm_from_beats, but over downbeats: a real
    bar-tracking model should produce evenly spaced downbeats when the
    track's meter is genuinely regular. ``coverage`` discounts a track
    where the model found only a handful of bars relative to how many
    4-beat bars the beat count implies, so a mostly-missing downbeat track
    cannot still score "reliable" purely from the few bars it did find
    being evenly spaced.
    """
    if len(downbeats) < MINIMUM_DOWNBEATS_FOR_METER:
        return 0.0
    bar_intervals = np.diff(np.sort(downbeats))
    bar_intervals = bar_intervals[bar_intervals > 0.0]
    if len(bar_intervals) == 0:
        return 0.0
    median_bar = float(np.median(bar_intervals))
    if median_bar <= 0.0:
        return 0.0
    deviation = float(np.median(np.abs(bar_intervals - median_bar)))
    consistency = max(0.0, min(1.0, 1.0 - (deviation / median_bar) * 3.0))
    expected_bars = max(1.0, len(beats) / 4.0)
    coverage = max(0.0, min(1.0, len(downbeats) / expected_bars))
    return max(0.0, min(1.0, consistency * (0.7 + 0.3 * coverage)))
