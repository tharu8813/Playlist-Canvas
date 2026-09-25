"""Sub-frame beat grids fitted to detected beat timestamps.

Beat trackers report beats on a frame grid (Beat This!: 50 fps, librosa:
~23 ms hops). The median inter-beat interval of such timestamps is itself
quantized: a true 128 BPM grid reads as 130.4 BPM (+1.9 %), 126 as 125.
Two such errors in opposite directions put a 16-bar beat-matched overlap
more than half a beat apart by its end. A least-squares line through the
beats (time = origin + index * period) averages the frame jitter away:
the same timestamps fit 128.000 BPM.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

MINIMUM_FIT_BEATS = 8
OUTLIER_FRACTION = 0.15
"""A beat further than this fraction of a period off the first fit is dropped for the refit."""


@dataclass(frozen=True, slots=True)
class BeatGrid:
    """``time = origin + n * period`` for integer beat numbers ``n``."""

    period: float
    origin: float

    @property
    def bpm(self) -> float:
        return 60.0 / self.period

    def snap(self, seconds: float) -> float:
        """The fitted grid beat nearest ``seconds``."""
        return self.origin + round((seconds - self.origin) / self.period) * self.period


def _beat_numbers(times: np.ndarray, period: float) -> np.ndarray:
    """Integer beat numbers, robust to a missed or doubled detection between two beats."""
    steps = np.rint(np.diff(times) / period)
    return np.concatenate(([0.0], np.cumsum(steps)))


def fit_beat_grid(beats: Sequence[float], start: float = -np.inf, end: float = np.inf) -> BeatGrid | None:
    """Least-squares grid through the beats in ``[start, end]``; the whole track if too few there.

    None when there are fewer than MINIMUM_FIT_BEATS beats to fit at all.
    """
    everything = np.asarray(beats, dtype=float)
    times = everything[(everything >= start) & (everything <= end)]
    if len(times) < MINIMUM_FIT_BEATS:
        times = everything
    if len(times) < MINIMUM_FIT_BEATS:
        return None
    intervals = np.diff(times)
    intervals = intervals[intervals > 0.0]
    if len(intervals) == 0:
        return None
    period = float(np.median(intervals))
    for _ in range(2):  # fit, drop outliers, refit
        numbers = _beat_numbers(times, period)
        keep = np.concatenate(([True], np.diff(numbers) > 0))  # drop doubled detections
        times, numbers = times[keep], numbers[keep]
        if len(times) < 2 or numbers[-1] <= 0:
            return None
        period, origin = (float(value) for value in np.polyfit(numbers, times, 1))
        if period <= 0.0:
            return None
        residual = np.abs(times - (origin + numbers * period))
        inliers = residual <= OUTLIER_FRACTION * period
        if inliers.all() or inliers.sum() < MINIMUM_FIT_BEATS:
            break
        times = times[inliers]
    return BeatGrid(period=period, origin=float(origin))


if __name__ == "__main__":
    # Frame-quantized 128 BPM beats with one missed and one doubled detection.
    truth = np.arange(0, 120, 60 / 128) + 0.013
    quantized = np.round(truth * 50) / 50
    quantized = np.delete(quantized, 40)
    quantized = np.sort(np.append(quantized, quantized[60] + 0.1))
    grid = fit_beat_grid(quantized)
    assert grid is not None and abs(grid.bpm - 128.0) < 0.01, grid
    assert abs(grid.snap(truth[100]) - truth[100]) < 0.005
    print("beatgrid ok", grid.bpm)
