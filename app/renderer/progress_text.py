"""Readable audio-preparation progress lines, in Korean or English (no Qt).

The renderers report plain English (``"Measuring loudness 12.3s / 900.0s · 1%"``);
the Export dialog and Preview's mix status show them through ``audio_progress_text``
as ``"최종 믹스 음량 측정 중 · 00:12 / 15:00"``. Unknown lines return None, so
callers keep their own translations for everything else.
"""

from __future__ import annotations

import re

from app.utils.time_format import format_clock

_TIMED = re.compile(r"^(?P<label>.+?) (?P<done>[\d.]+)s / (?P<total>[\d.]+)s(?: · \d+%)?$")
_COUNTED = re.compile(r"^(?P<label>.+?) (?P<done>\d+)/(?P<total>\d+)$")

_TIMED_LABELS = {
    "Combining mix": ("믹싱 중", "Mixing"),
    "Measuring loudness": ("최종 믹스 음량 측정 중", "Measuring the mix loudness"),
    "Normalizing loudness and saving audio": ("음량 보정 및 오디오 저장 중", "Normalizing loudness and saving audio"),
}
_COUNTED_LABELS = {
    "Analyzing beats and vocals": ("리듬·보컬 분석 중", "Analyzing beats and vocals"),
    "Analyzing track structure": ("곡 구조 분석 중", "Analyzing song structure"),
}
_FIXED = {
    "Analyzing tracks for AutoMix": ("AutoMix 분석 준비 중", "Preparing AutoMix analysis"),
    "Analyzing track structure": ("곡 구조 분석 중", "Analyzing song structure"),
    "Rendering AutoMix transitions": ("전환 구간 믹싱 준비 중", "Preparing to mix the transitions"),
    "Preparing AutoMix audio": ("믹싱 준비 중", "Preparing the mix"),
    "Validating mixed audio": ("믹스 결과 검증 중", "Checking the mixed audio"),
    "AutoMix audio ready": ("믹스 완료", "Mix done"),
}


def clock(seconds: float) -> str:
    return format_clock(round(seconds))


def audio_progress_text(message: str, korean: bool) -> str | None:
    """``message`` as a user-facing line, or None when it is not an audio-preparation line."""
    pick = 0 if korean else 1
    if message in _FIXED:
        return _FIXED[message][pick]
    if match := _TIMED.match(message):
        label = _TIMED_LABELS.get(match["label"])
        if label is not None:
            return f"{label[pick]} · {clock(float(match['done']))} / {clock(float(match['total']))}"
    if match := _COUNTED.match(message):
        label = _COUNTED_LABELS.get(match["label"])
        if label is not None:
            unit = "곡 완료" if korean else " tracks done"
            return f"{label[pick]} · {match['done']}/{match['total']}{unit}"
    if message.startswith("Preparing ") and message.endswith(" clip(s)"):
        return "믹싱 준비 중" if korean else "Preparing the mix"
    return None
