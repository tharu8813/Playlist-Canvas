"""UI-independent audio timeline models and a legacy playlist adapter.

These are runtime snapshots, not a new project storage format. Overlaps and
transitions can be represented here; the legacy playback/export backends still
consume PlaylistTrack and do not render V2 edits yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite

from app.models.playlist import PlaylistTrack
from app.timeline.track_schedule import resolve_track_windows


def _nonnegative(**values: float) -> None:
    for name, value in values.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0):
            raise ValueError(f"{name} must be a finite non-negative number.")


@dataclass(frozen=True, slots=True)
class AudioClip:
    """A source interval placed independently on the timeline, in seconds."""

    id: str
    track_id: str
    timeline_start: float
    source_in: float
    source_out: float
    playback_rate: float = 1.0
    gain: float = 1.0
    enabled: bool = True

    def __post_init__(self) -> None:
        _nonnegative(timeline_start=self.timeline_start, source_in=self.source_in,
                     source_out=self.source_out, playback_rate=self.playback_rate,
                     gain=self.gain)
        if self.source_out < self.source_in or self.playback_rate == 0:
            raise ValueError("A clip needs ordered source bounds and a positive playback rate.")
        if not all(isinstance(value, str) and value.strip() for value in (self.id, self.track_id)):
            raise ValueError("Clip and playlist track IDs must be non-empty strings.")
        if not isinstance(self.enabled, bool):
            raise ValueError("Clip enabled must be a boolean.")
        if not isfinite(self.timeline_end):
            raise ValueError("Clip timeline end must be finite.")

    @property
    def duration(self) -> float:
        return (self.source_out - self.source_in) / self.playback_rate

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.duration


class TransitionType(str, Enum):
    CUT = "cut"
    CROSSFADE = "crossfade"
    EQUAL_POWER = "equal_power"
    BEAT_MATCH = "beat_match"
    AUTOMIX = "automix"


@dataclass(frozen=True, slots=True)
class AudioTransition:
    """A transition description; DSP implementation belongs to a later phase."""

    clip_a: str
    clip_b: str
    start: float
    duration: float
    type: TransitionType = TransitionType.CUT

    def __post_init__(self) -> None:
        _nonnegative(start=self.start, duration=self.duration)
        if not all(isinstance(value, str) and value.strip() for value in (self.clip_a, self.clip_b)):
            raise ValueError("Transition clip IDs must be non-empty strings.")
        if self.clip_a == self.clip_b or not isinstance(self.type, TransitionType):
            raise ValueError("A transition needs distinct clips and a supported type.")
        if not isfinite(self.start + self.duration):
            raise ValueError("Transition end must be finite.")


@dataclass(frozen=True, slots=True)
class AudioTrack:
    """An audio lane, distinct from a playlist media entry; overlaps are allowed."""

    id: str
    clips: tuple[AudioClip, ...] = ()

    @property
    def duration(self) -> float:
        return max((clip.timeline_end for clip in self.clips), default=0.0)


@dataclass(frozen=True, slots=True)
class Timeline:
    """Placement snapshot. Duration includes disabled clips, like the editor."""

    audio_tracks: tuple[AudioTrack, ...] = ()
    transitions: tuple[AudioTransition, ...] = ()

    def __post_init__(self) -> None:
        lane_ids = [track.id for track in self.audio_tracks]
        clip_ids = [clip.id for track in self.audio_tracks for clip in track.clips]
        if len(lane_ids) != len(set(lane_ids)) or len(clip_ids) != len(set(clip_ids)):
            raise ValueError("Timeline lane and clip IDs must be unique.")
        known = set(clip_ids)
        if any(t.clip_a not in known or t.clip_b not in known for t in self.transitions):
            raise ValueError("Transition references an unknown clip.")

    @property
    def duration(self) -> float:
        return max((track.duration for track in self.audio_tracks), default=0.0)


def timeline_from_playlist(
    tracks: Sequence[PlaylistTrack], *, enabled_only: bool = False,
) -> Timeline:
    """Adapt legacy order without changing its scheduling or mutating its tracks.

    The editor includes disabled entries. Render callers can request enabled
    entries only, filtering *before* scheduling just as legacy export does.
    IDs are stable across repeated snapshots and playlist reordering.
    """
    selected = [track for track in tracks if track.enabled or not enabled_only]
    clips = tuple(
        AudioClip(id=f"playlist:{window.track.id}", track_id=window.track.id,
                  timeline_start=window.start, source_in=0.0,
                  source_out=window.track.duration_seconds, enabled=window.track.enabled)
        for window in resolve_track_windows(selected)
    )
    return Timeline(audio_tracks=(AudioTrack(id="playlist", clips=clips),))
