"""Manual AutoMix transitions: what the user set for one junction instead of "auto".

A junction is keyed by its (outgoing track id, incoming track id) pair, so an
override only applies while those two tracks are neighbours: reordering the
playlist puts the junction back on automatic, and putting the pair back next
to each other brings the saved override back. Stored in the project file
(``ProjectSettings.automix_overrides``) as plain JSON, and handed to the
planner through ``AutoMixTransitionSettings.overrides``.

Times are in each track's own source seconds, so the values stay meaningful
when an earlier junction changes and the timeline shifts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

STYLE_AUTO = "auto"
"""Keep the automatic style selector for this window (analysis decides)."""
STYLE_LEGACY = "legacy"
"""A plain full-band equal-power crossfade."""
STYLE_CUT = "cut"
"""No overlap: the outgoing track stops at the cue and the next one starts."""
MANUAL_STYLES = (
    STYLE_AUTO, "bass_swap", "vocal_safe_eq", "filter_sweep", "filter_blend",
    "short_fade", "drop_in", STYLE_LEGACY, STYLE_CUT,
)
MAX_DURATION_SECONDS = 60.0
MAX_OVERRIDES = 20_000


def pair_key(outgoing_track_id: str, incoming_track_id: str) -> str:
    """The storage key of the junction between two neighbouring tracks."""
    return f"{outgoing_track_id}>{incoming_track_id}"


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


@dataclass(frozen=True, slots=True)
class TransitionOverride:
    """One manually set junction.

    ``outgoing_cue``: where in the outgoing track the overlap starts.
    ``incoming_cue``: where in the incoming track playback starts (the
    beginning of the overlap). ``duration``: overlap length on the timeline;
    ignored for ``cut``. ``tempo_match``: ease the outgoing track onto the
    incoming tempo, as the automatic beat match does (needs both BPMs).
    ``vocal_handoff``: vocal_safe_eq only, 0..1 of the window where the mid
    band changes hands (``None``: the style's default).
    """

    outgoing_cue: float
    incoming_cue: float = 0.0
    duration: float = 8.0
    style: str = STYLE_AUTO
    tempo_match: bool = True
    vocal_handoff: float | None = None

    def __post_init__(self) -> None:
        if not _number(self.outgoing_cue) or self.outgoing_cue < 0.0:
            raise ValueError("Manual transition outgoing_cue must be a finite, non-negative number.")
        if not _number(self.incoming_cue) or self.incoming_cue < 0.0:
            raise ValueError("Manual transition incoming_cue must be a finite, non-negative number.")
        if not _number(self.duration) or not 0.0 <= self.duration <= MAX_DURATION_SECONDS:
            raise ValueError("Manual transition duration must be between 0 and 60 seconds.")
        if self.style not in MANUAL_STYLES:
            raise ValueError(f"Unknown manual transition style {self.style!r}.")
        if not isinstance(self.tempo_match, bool):
            raise ValueError("Manual transition tempo_match must be a boolean.")
        if self.vocal_handoff is not None and (
            not _number(self.vocal_handoff) or not 0.1 <= self.vocal_handoff <= 0.9
        ):
            raise ValueError("Manual transition vocal_handoff must be between 0.1 and 0.9.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "outgoing_cue": float(self.outgoing_cue),
            "incoming_cue": float(self.incoming_cue),
            "duration": float(self.duration),
            "style": self.style,
            "tempo_match": self.tempo_match,
            "vocal_handoff": None if self.vocal_handoff is None else float(self.vocal_handoff),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TransitionOverride":
        if not isinstance(data, Mapping):
            raise ValueError("A manual transition must be an object.")
        return cls(
            outgoing_cue=data.get("outgoing_cue", -1.0),
            incoming_cue=data.get("incoming_cue", 0.0),
            duration=data.get("duration", 8.0),
            style=data.get("style", STYLE_AUTO),
            tempo_match=data.get("tempo_match", True),
            vocal_handoff=data.get("vocal_handoff"),
        )


def parse_overrides(data: object) -> dict[str, TransitionOverride]:
    """Valid entries of a saved ``automix_overrides`` object; a broken entry is dropped, not fatal."""
    if not isinstance(data, Mapping):
        return {}
    parsed: dict[str, TransitionOverride] = {}
    for key, value in list(data.items())[:MAX_OVERRIDES]:
        # Keys are only ever compared whole (settings.override_for builds the
        # same pair_key), so a track id that itself contains ">" is fine.
        if not isinstance(key, str) or not any(
            key[:index] and key[index + 1:] for index, char in enumerate(key) if char == ">"
        ):
            continue
        try:
            parsed[key] = TransitionOverride.from_dict(value)
        except (TypeError, ValueError, OverflowError):
            continue
    return parsed


def serialize_overrides(overrides: Mapping[str, TransitionOverride]) -> dict[str, dict[str, Any]]:
    return {key: override.to_dict() for key, override in overrides.items()}
