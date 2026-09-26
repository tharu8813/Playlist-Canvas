"""Shared clock-style duration formatting.

Many dialogs, services and templates show a duration as ``MM:SS`` and switch
to ``HH:MM:SS`` once it reaches an hour. They used to carry their own copies
of that divmod chain; they now call :func:`format_clock` instead.

Callers still decide how fractional seconds become whole seconds, because
that differs on purpose: playback clocks truncate (``int``) so the display
never runs ahead of the audio, while ETA/total texts round to the nearest
second.
"""

from __future__ import annotations


def format_clock(total_seconds: int, *, hours: bool = True) -> str:
    """Format whole seconds as ``MM:SS``, or ``HH:MM:SS`` from one hour on.

    Negative input clamps to ``00:00``. With ``hours=False`` the hour part is
    never split out, so long durations keep counting minutes (``75:00``).
    """
    total = max(0, total_seconds)
    minutes, seconds = divmod(total, 60)
    if hours and minutes >= 60:
        whole_hours, minutes = divmod(minutes, 60)
        return f"{whole_hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
