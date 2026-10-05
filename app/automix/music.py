"""Local YAMNet music tagging and conservative, cue-specific mixing context.

Audio stays on the computer. The bundled ONNX graph includes the original
16 kHz frontend; no TensorFlow dependency or runtime model downloads.
"""
from __future__ import annotations

import csv
import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

from app.automix.analysis.provider import AnalysisCancelled
from app.automix.models import MusicTagRegion, TrackAnalysis

LOGGER = logging.getLogger(__name__)
TAG_THRESHOLD = 0.35
WINDOW_SECONDS = 8.0
EDGE_SECONDS = 60.0
GENTLE_GENRES = {"Classical music", "Opera", "Jazz", "Folk music", "Ambient music", "New-age music", "Lullaby"}
DRIVEN_GENRES = {"Electronic music", "House music", "Techno", "Dubstep", "Drum and bass", "Electronica",
                 "Electronic dance music", "Trance music", "Disco", "Dance music"}
_SESSION = None
_SESSION_LOCK = threading.Lock()


def model_directory() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    return (Path(bundle) / "app/automix/analysis/models" if bundle
            else Path(__file__).parent / "analysis/models")


def _session():
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = min(2, os.cpu_count() or 1)
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            _SESSION = onnxruntime.InferenceSession(
                str(model_directory() / "yamnet.onnx"), options, providers=["CPUExecutionProvider"])
    return _SESSION


def analyze_music(signal: np.ndarray, sample_rate: int, duration: float,
                  cancel_event: threading.Event) -> tuple[MusicTagRegion, ...]:
    """Tag head/tail and a middle sample, bounded to ~136 seconds of inference.

    ponytail: sampled coverage; increase coverage if cues beyond the measured
    windows become common. Unmeasured windows are never given a global label.
    """
    if cancel_event.is_set():
        raise AnalysisCancelled("AutoMix music tagging cancelled.")
    if not (model_directory() / "yamnet.onnx").is_file():
        return ()
    try:
        from math import gcd
        from scipy.signal import resample_poly

        session = _session()
        with (model_directory() / "yamnet_class_map.csv").open(encoding="utf-8") as file:
            labels = [r["display_name"] for r in csv.DictReader(file)]
        if len(labels) != 521:
            raise ValueError("YAMNet class map must contain 521 labels.")
        duration = min(duration, len(signal) / sample_rate)
        if duration <= 0.0:
            return ()
        windows = sorted([(0.0, min(duration, EDGE_SECONDS)),
                          (max(0.0, duration / 2 - WINDOW_SECONDS), min(duration, duration / 2 + WINDOW_SECONDS)),
                          (max(0.0, duration - EDGE_SECONDS), duration)])
        merged = []
        for start, end in windows:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        divisor = gcd(sample_rate, 16000)
        regions = []
        for start, end in merged:
            while start < end:
                if cancel_event.is_set():
                    raise AnalysisCancelled("AutoMix music tagging cancelled.")
                stop = min(end, start + WINDOW_SECONDS)
                chunk = signal[round(start * sample_rate):round(stop * sample_rate)]
                waveform = resample_poly(chunk, 16000 // divisor, sample_rate // divisor).astype(np.float32)
                scores = session.run(["output_0"], {"waveform": waveform})[0]
                if scores.ndim != 2 or scores.shape[1] != 521 or not np.isfinite(scores).all():
                    raise ValueError("YAMNet returned invalid music scores.")
                means = np.clip(scores.mean(axis=0), 0.0, 1.0)

                def tags(indices):
                    return tuple(sorted(((labels[i], round(float(means[i]), 4)) for i in indices
                                         if means[i] >= 0.05), key=lambda tag: (-tag[1], tag[0]))[:5])

                regions.append(MusicTagRegion(start, stop, tags((*range(211, 261), 266, 269)), tags(range(271, 277))))
                start = stop
        if cancel_event.is_set():
            raise AnalysisCancelled("AutoMix music tagging cancelled.")
        return tuple(regions)
    except AnalysisCancelled:
        raise
    except Exception as error:  # one optional classifier must never prevent the rhythm analysis
        LOGGER.warning("AutoMix genre/mood tagging unavailable (%s); music context stays unknown.", error)
        return ()


def music_tags_at(analysis: TrackAnalysis, start: float, end: float) -> MusicTagRegion | None:
    """Duration-weighted tags when at least 80% of this source window was measured."""
    if end <= start or start < 0.0:
        return None
    genres, moods = {}, {}
    coverage = 0.0
    for region in analysis.music_tags:
        weight = max(0.0, min(end, region.end_seconds) - max(start, region.start_seconds))
        if not weight:
            continue
        coverage += weight
        for target, tags in ((genres, region.genres), (moods, region.moods)):
            for name, score in tags:
                target[name] = target.get(name, 0.0) + weight * score
    if coverage < 0.8 * (end - start):
        return None
    return MusicTagRegion(start, end,
                          tuple((name, value / coverage) for name, value in sorted(genres.items())),
                          tuple((name, value / coverage) for name, value in sorted(moods.items())))


def character(tags: MusicTagRegion | None) -> tuple[float, float, float]:
    """(gentle, driven, rap) evidence; sub-threshold predictions have no effect."""
    if tags is None:
        return 0.0, 0.0, 0.0
    genres, moods = dict(tags.genres), dict(tags.moods)
    gentle = max([genres.get(g, 0.0) for g in GENTLE_GENRES] + [moods.get(m, 0.0) for m in ("Sad music", "Tender music")])
    driven = max([genres.get(g, 0.0) for g in DRIVEN_GENRES] + [moods.get(m, 0.0) for m in ("Exciting music", "Angry music")])
    return tuple(v if v >= TAG_THRESHOLD else 0.0 for v in (gentle, driven, genres.get("Hip hop music", 0.0)))


def context_score(outgoing: TrackAnalysis, incoming: TrackAnalysis, out_start: float, out_end: float,
                  in_start: float, in_end: float, duration: float, max_duration: float) -> tuple[float, tuple[str, ...]]:
    """A soft length preference; rhythm, bounds and vocal safety remain authoritative."""
    out_tags = music_tags_at(outgoing, out_start, out_end)
    in_tags = music_tags_at(incoming, in_start, in_end)
    a, b = character(out_tags), character(in_tags)
    reach = min(1.0, duration / max(0.01, max_duration))
    if out_tags is not None and in_tags is not None:
        out_moods, in_moods = dict(out_tags.moods), dict(in_tags.moods)
        opposite = max(
            min(out_moods.get("Happy music", 0.0), in_moods.get(mood, 0.0))
            for mood in ("Sad music", "Angry music", "Scary music"))
        opposite = max(opposite, max(
            min(in_moods.get("Happy music", 0.0), out_moods.get(mood, 0.0))
            for mood in ("Sad music", "Angry music", "Scary music")))
        if opposite >= TAG_THRESHOLD:
            return -0.35 * opposite * reach, ("- opposing moods: a shorter blend preserves the emotional change",)
    if (a[0] and b[1]) or (a[1] and b[0]):
        strength = max(min(a[0], b[1]), min(a[1], b[0]))
        return -0.35 * strength * reach, ("- contrasting genre/mood: a shorter blend preserves each character",)
    if max(a[2], b[2]):
        return -0.35 * max(a[2], b[2]) * reach, ("- hip hop: prefer a concise vocal handoff",)
    if min(a[1], b[1]):
        return 0.12 * min(a[1], b[1]) * reach, ("+ shared driving genre/mood: room for a sustained beat blend",)
    if min(a[0], b[0]):
        return 0.10 * min(a[0], b[0]) * reach, ("+ shared gentle genre/mood: a gradual blend",)
    return 0.0, ()


def tags_text(tags: MusicTagRegion | None, *, moods: bool = False) -> str | None:
    if tags is None:
        return None
    values = sorted(tags.moods if moods else tags.genres, key=lambda tag: (-tag[1], tag[0]))
    selected = [f"{label} ({score:.0%})" for label, score in values if score >= TAG_THRESHOLD][:3]
    return ", ".join(selected) or None
