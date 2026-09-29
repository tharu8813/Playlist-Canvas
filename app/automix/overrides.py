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
STYLE_EQ = "eq"
"""Hand-set band timing: when each band (lows, mids, highs) of each track fades."""
MANUAL_STYLES = (
    STYLE_AUTO, "bass_swap", "vocal_safe_eq", "filter_sweep", "filter_blend",
    "short_fade", "drop_in", "echo_out", "tape_stop", "downbeat_cut", STYLE_LEGACY, STYLE_CUT, STYLE_EQ,
)
STYLE_ALIASES = {"legacy": "short_fade", STYLE_CUT: "downbeat_cut"}
"""Saved styles the editor shows as another one: a plain crossfade is the short
fade without its peak limiter, a cut the downbeat cut without its click guard.
Both still load and plan exactly as saved."""
ECHO_BEAT_CHOICES = (0.5, 1.0, 2.0)
MAX_DURATION_SECONDS = 60.0
MAX_RAMP_SECONDS = 120.0
MAX_ECHO_FEEDBACK = 0.95
"""Each echo repeat at most 0.4 dB under the one before: a tail that rings for many bars."""
MAX_OVERRIDES = 20_000
EQ_BANDS = ("low", "mid", "high")
"""The renderer's crossover bands, in the order ``TransitionOverride.eq_bands`` stores them."""
MIN_EQ_WINDOW = 0.02
"""Shortest band fade, as a fraction of the overlap (a zero-length afade is not a fade)."""

Window = tuple[float, float]
BandWindows = tuple[tuple[Window, Window], ...]
"""Per band in EQ_BANDS order: ((outgoing fade start, end), (incoming fade start, end)),
each 0..1 of the overlap -- the renderer's BAND_ENVELOPES shape."""


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
    ``eq_bands``: eq only, each band's fade windows (see ``BandWindows``);
    ``None``: start from the bass swap's.
    ``echo_beats``/``echo_feedback``/``echo_low_cut``: echo_out only -- the
    delay in outgoing beats, each repeat's level against the previous one,
    and whether the repeats lose their lows.
    ``tape_entry``: tape_stop only, 0..1 of the window where the incoming song enters.
    ``key_shift``: semitones the outgoing tail glides by to meet the incoming
    key (0: never; ``None``: automatic, as the planner decides).
    ``ramp_seconds``: with a tempo match (or key glide), how long -- in outgoing
    source seconds before the cue -- the outgoing track takes to reach the new
    tempo (0: at once; ``None``: automatic, 8 bars or 16 for a tempo bridge).
    """

    outgoing_cue: float
    incoming_cue: float = 0.0
    duration: float = 8.0
    style: str = STYLE_AUTO
    tempo_match: bool = True
    vocal_handoff: float | None = None
    eq_bands: BandWindows | None = None
    echo_beats: float = 1.0
    echo_feedback: float = 0.55
    echo_low_cut: bool = True
    tape_entry: float = 0.6
    key_shift: int | None = None
    ramp_seconds: float | None = None

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
        if self.eq_bands is not None:
            object.__setattr__(self, "eq_bands", _band_windows(self.eq_bands))
        if not _number(self.echo_beats) or self.echo_beats not in ECHO_BEAT_CHOICES:
            raise ValueError("Manual transition echo_beats must be one of 0.5, 1, 2 beats.")
        if not _number(self.echo_feedback) or not 0.2 <= self.echo_feedback <= MAX_ECHO_FEEDBACK:
            raise ValueError("Manual transition echo_feedback must be between 0.2 and 0.95.")
        if not isinstance(self.echo_low_cut, bool):
            raise ValueError("Manual transition echo_low_cut must be a boolean.")
        if not _number(self.tape_entry) or not 0.2 <= self.tape_entry <= 0.95:
            raise ValueError("Manual transition tape_entry must be between 0.2 and 0.95.")
        if self.key_shift is not None and (
            not isinstance(self.key_shift, int) or isinstance(self.key_shift, bool) or abs(self.key_shift) > 2
        ):
            raise ValueError("Manual transition key_shift must be a whole number of semitones within 2.")
        if self.ramp_seconds is not None and (
            not _number(self.ramp_seconds) or not 0.0 <= self.ramp_seconds <= MAX_RAMP_SECONDS
        ):
            raise ValueError("Manual transition ramp_seconds must be between 0 and 120 seconds.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "outgoing_cue": float(self.outgoing_cue),
            "incoming_cue": float(self.incoming_cue),
            "duration": float(self.duration),
            "style": self.style,
            "tempo_match": self.tempo_match,
            "vocal_handoff": None if self.vocal_handoff is None else float(self.vocal_handoff),
            "echo_beats": float(self.echo_beats),
            "echo_feedback": float(self.echo_feedback),
            "echo_low_cut": self.echo_low_cut,
            "tape_entry": float(self.tape_entry),
            "key_shift": self.key_shift,
            "ramp_seconds": None if self.ramp_seconds is None else float(self.ramp_seconds),
        }
        if self.eq_bands is not None:
            data["eq"] = {
                band: {"out": list(out_window), "in": list(in_window)}
                for band, (out_window, in_window) in zip(EQ_BANDS, self.eq_bands)
            }
        return data

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
            eq_bands=_eq_from_dict(data.get("eq")),
            echo_beats=data.get("echo_beats", 1.0),
            echo_feedback=data.get("echo_feedback", 0.55),
            echo_low_cut=data.get("echo_low_cut", True),
            tape_entry=data.get("tape_entry", 0.6),
            key_shift=data.get("key_shift"),
            ramp_seconds=data.get("ramp_seconds"),
        )


def _window(value: object) -> Window:
    if not isinstance(value, (tuple, list)) or len(value) != 2 or not all(_number(v) for v in value):
        raise ValueError("A band fade window must be two numbers.")
    start, end = float(value[0]), float(value[1])
    if not (0.0 <= start and end <= 1.0 and end - start >= MIN_EQ_WINDOW - 1e-9):
        raise ValueError("A band fade window must lie within 0..1 and not be empty.")
    return start, end


def _band_windows(value: object) -> BandWindows:
    """``value`` checked and normalized to float tuples, or ValueError."""
    if not isinstance(value, (tuple, list)) or len(value) != len(EQ_BANDS):
        raise ValueError("Manual transition eq_bands needs one entry per band (low, mid, high).")
    bands = []
    for entry in value:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError("Each band needs an outgoing and an incoming fade window.")
        bands.append((_window(entry[0]), _window(entry[1])))
    return tuple(bands)


def _eq_from_dict(value: object) -> BandWindows | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Manual transition eq must be an object.")
    bands = []
    for band in EQ_BANDS:
        entry = value.get(band)
        if not isinstance(entry, Mapping):
            raise ValueError(f"Manual transition eq is missing the {band} band.")
        bands.append((entry.get("out"), entry.get("in")))
    return _band_windows(bands)


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
