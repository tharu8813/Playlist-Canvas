"""Driver-independent health state for the interactive GPU preview."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GpuHealthStats:
    presented_frames: int = 0
    total_stalls: int = 0
    consecutive_stalls: int = 0
    last_latency_ms: float = 0.0
    maximum_latency_ms: float = 0.0
    awaiting_frame: bool = False


class GpuPreviewHealth:
    """Trip only after repeated presentation stalls, not a single slow frame."""

    def __init__(self, fallback_after_stalls: int = 2) -> None:
        self.fallback_after_stalls = max(1, int(fallback_after_stalls))
        self._queued_at = 0.0
        self._awaiting = False
        self._presented_frames = 0
        self._total_stalls = 0
        self._consecutive_stalls = 0
        self._last_latency_ms = 0.0
        self._maximum_latency_ms = 0.0

    @property
    def stats(self) -> GpuHealthStats:
        return GpuHealthStats(
            presented_frames=self._presented_frames,
            total_stalls=self._total_stalls,
            consecutive_stalls=self._consecutive_stalls,
            last_latency_ms=self._last_latency_ms,
            maximum_latency_ms=self._maximum_latency_ms,
            awaiting_frame=self._awaiting,
        )

    def frame_queued(self, now_seconds: float) -> None:
        self._queued_at = float(now_seconds)
        self._awaiting = True

    def frame_presented(self, now_seconds: float) -> None:
        if self._awaiting:
            latency = max(0.0, (float(now_seconds) - self._queued_at) * 1000.0)
            self._last_latency_ms = latency
            self._maximum_latency_ms = max(self._maximum_latency_ms, latency)
        self._awaiting = False
        self._presented_frames += 1
        self._consecutive_stalls = 0

    def frame_timed_out(self) -> bool:
        """Record a watchdog expiry and return whether CPU fallback should trip."""
        if not self._awaiting:
            return False
        self._awaiting = False
        self._total_stalls += 1
        self._consecutive_stalls += 1
        return self._consecutive_stalls >= self.fallback_after_stalls

    def cancel_wait(self) -> None:
        self._awaiting = False

    def reset(self) -> None:
        threshold = self.fallback_after_stalls
        self.__init__(threshold)
