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


def ease_in_out_cubic(value: float) -> float:
    """Balanced motion that stays visible throughout an exit transition."""
    progress = clamp_progress(value)
    if progress < 0.5:
        return 4.0 * progress ** 3
    return 1.0 - ((-2.0 * progress + 2.0) ** 3) / 2.0


def slide_distance(width: float, height: float) -> float:
    """Return restrained travel that scales without flying across the Canvas."""
    return min(112.0, max(28.0, max(float(width), float(height)) * 0.095))


def hidden_opacity_factor(style: str) -> float:
    """Every disappearance reaches transparency, avoiding an end-frame pop."""
    return 0.0 if style != "none" else 1.0


def hidden_scale_factor(style: str) -> float:
    return {
        "zoom": 0.90,
        "pop": 0.82,
        "rotate": 0.96,
    }.get(style, 1.0)


def hidden_rotation_offset(style: str, entering: bool) -> float:
    """Return a restrained rotation that unwinds or winds up with the fade."""
    if style != "rotate":
        return 0.0
    return -12.0 if entering else 12.0
