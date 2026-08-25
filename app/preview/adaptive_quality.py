"""Hysteresis-based render scaling for interactive GPU previews."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdaptiveQualityState:
    scale: float
    factor: float
    level: int
    changed: bool


class AdaptivePreviewQuality:
    """Reduce preview pixels under sustained load and recover conservatively."""

    FACTORS = (1.0, 0.85, 0.70, 0.55)

    def __init__(self, base_scale: float = 0.65) -> None:
        self.base_scale = max(0.3, min(1.0, float(base_scale)))
        self.level = 0
        self._overloaded_samples = 0
        self._stable_samples = 0

    @property
    def factor(self) -> float:
        return self.FACTORS[self.level]

    @property
    def scale(self) -> float:
        return max(0.3, round(self.base_scale * self.factor, 3))

    def reset(self, base_scale: float | None = None) -> AdaptiveQualityState:
        previous = self.scale
        if base_scale is not None:
            self.base_scale = max(0.3, min(1.0, float(base_scale)))
        self.level = 0
        self._overloaded_samples = 0
        self._stable_samples = 0
        return self._state(previous != self.scale)

    def observe(
        self, actual_fps: float, target_fps: int, *, dropped_frames: int = 0,
    ) -> AdaptiveQualityState:
        """Consume one rolling FPS sample and return the current render scale."""
        previous = self.scale
        target = max(1.0, float(target_fps))
        overloaded = dropped_frames > 0 or (
            actual_fps > 0.0 and actual_fps < target * 0.78
        )
        stable = (
            dropped_frames == 0 and actual_fps >= target * 0.93
        )
        if overloaded:
            self._overloaded_samples += 1
            self._stable_samples = 0
            if self._overloaded_samples >= 2 and self.level < len(self.FACTORS) - 1:
                self.level += 1
                self._overloaded_samples = 0
        elif stable:
            self._stable_samples += 1
            self._overloaded_samples = 0
            if self._stable_samples >= 6 and self.level > 0:
                self.level -= 1
                self._stable_samples = 0
        else:
            self._overloaded_samples = 0
            self._stable_samples = 0
        return self._state(previous != self.scale)

    def _state(self, changed: bool) -> AdaptiveQualityState:
        return AdaptiveQualityState(
            self.scale, self.factor, self.level, changed,
        )
