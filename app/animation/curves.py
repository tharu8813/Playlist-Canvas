"""Shared motion curves and geometry for Canvas preview and video export."""

from __future__ import annotations


def clamp_progress(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def ease_out_quint(value: float) -> float:
    """Fast response with a long, soft landing for entrances."""
    progress = clamp_progress(value)
    return 1.0 - (1.0 - progress) ** 5


def ease_out_cubic(value: float) -> float:
    """Balanced reveal used for lyric changes where quint feels too abrupt."""
    progress = clamp_progress(value)
    return 1.0 - (1.0 - progress) ** 3


def ease_in_quint(value: float) -> float:
    """Soft departure that accelerates naturally toward the end."""
    progress = clamp_progress(value)
    return progress ** 5


def slide_distance(width: float, height: float) -> float:
    """Return restrained travel that scales without flying across the Canvas."""
    return min(96.0, max(24.0, max(float(width), float(height)) * 0.085))


def hidden_opacity_factor(style: str) -> float:
    """Every disappearance reaches transparency, avoiding an end-frame pop."""
    return 0.0 if style != "none" else 1.0


def hidden_scale_factor(style: str) -> float:
    return 0.92 if style == "zoom" else 1.0
