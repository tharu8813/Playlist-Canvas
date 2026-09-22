"""Immutable structural analysis result model.

``TrackStructureAnalysis`` is cache data, never primary project document
data -- same "Persistence invariant" as ``TrackAnalysis``
(app/automix/models.py): derived from an audio file, always recomputable,
never stored inside a ``PlaylistTrack`` or project JSON.

Deliberately a separate model from ``TrackAnalysis`` rather than more
fields bolted onto it: ``TrackAnalysis`` is rhythm/key/energy/vocal-
activity output (Beat This!/Basic); this is structure-provider (Sonara)
output -- intro/outro/section/energy-curve timeline data, a materially
different shape (time-series/segment data vs. mostly scalars) and produced
by an entirely separate, independently-optional analyzer. Neither
provider overwrites the other's fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


@dataclass(frozen=True, slots=True)
class TrackSection:
    """One contiguous structural section, exactly as a structure provider reports it.

    ``label`` stays ``None`` unless a provider genuinely names sections
    (verse/chorus/...) -- never fabricated locally from boundary/energy
    data alone.
    """

    start_seconds: float
    end_seconds: float
    energy: float | None = None
    label: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not _is_finite_number(self.start_seconds) or self.start_seconds < 0.0:
            raise ValueError("TrackSection.start_seconds must be finite and non-negative.")
        if not _is_finite_number(self.end_seconds) or self.end_seconds <= self.start_seconds:
            raise ValueError("TrackSection.end_seconds must be finite and greater than start_seconds.")
        if self.energy is not None and (not _is_finite_number(self.energy) or self.energy < 0.0):
            raise ValueError("TrackSection.energy must be a finite, non-negative number when known.")
        if self.label is not None and (not isinstance(self.label, str) or not self.label.strip()):
            raise ValueError("TrackSection.label must be a non-empty string when known.")
        if self.confidence is not None and (
            not _is_finite_number(self.confidence) or not (0.0 <= self.confidence <= 1.0)
        ):
            raise ValueError("TrackSection.confidence must be a finite number between 0.0 and 1.0 when known.")


@dataclass(frozen=True, slots=True)
class TrackStructureAnalysis:
    """One track's structural analysis: intro/outro boundaries, sections, energy curve.

    Every field beyond ``track_id``/``source_path``/``duration_seconds`` is
    optional: a provider that only reports an energy curve, or only
    intro/outro, leaves the rest at defaults rather than fabricating
    values -- same discipline as ``TrackAnalysis``.

    ``intro_end_seconds``/``outro_start_seconds`` are heuristic structure-
    analysis output, not ground truth -- stored as-is here. Nothing in
    this phase treats them as a forced transition point; a future planner
    may use them as a scoring bonus anchor.
    """

    track_id: str
    source_path: str
    duration_seconds: float

    intro_end_seconds: float | None = None
    outro_start_seconds: float | None = None

    sections: tuple[TrackSection, ...] = ()

    energy_curve: tuple[float, ...] = ()
    energy_curve_hop_seconds: float | None = None

    analyzer_id: str = ""
    analyzer_version: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.track_id, str) or not self.track_id.strip():
            raise ValueError("TrackStructureAnalysis.track_id must be a non-empty string.")
        if not isinstance(self.source_path, str) or not self.source_path.strip():
            raise ValueError("TrackStructureAnalysis.source_path must be a non-empty string.")
        if not _is_finite_number(self.duration_seconds) or self.duration_seconds < 0.0:
            raise ValueError("TrackStructureAnalysis.duration_seconds must be finite and non-negative.")
        if self.intro_end_seconds is not None and not (
            _is_finite_number(self.intro_end_seconds) and 0.0 <= self.intro_end_seconds <= self.duration_seconds
        ):
            raise ValueError("TrackStructureAnalysis.intro_end_seconds must be within [0, duration].")
        if self.outro_start_seconds is not None and not (
            _is_finite_number(self.outro_start_seconds) and 0.0 <= self.outro_start_seconds <= self.duration_seconds
        ):
            raise ValueError("TrackStructureAnalysis.outro_start_seconds must be within [0, duration].")
        previous_end = float("-inf")
        for section in self.sections:
            if section.end_seconds > self.duration_seconds:
                raise ValueError("TrackStructureAnalysis section end must not exceed the track duration.")
            if section.start_seconds < previous_end:
                raise ValueError("TrackStructureAnalysis sections must be sorted and non-overlapping.")
            previous_end = section.end_seconds
        if self.energy_curve_hop_seconds is not None and (
            not _is_finite_number(self.energy_curve_hop_seconds) or self.energy_curve_hop_seconds <= 0.0
        ):
            raise ValueError("TrackStructureAnalysis.energy_curve_hop_seconds must be positive when known.")
        if self.energy_curve and self.energy_curve_hop_seconds is None:
            raise ValueError("TrackStructureAnalysis.energy_curve requires energy_curve_hop_seconds.")
        for value in self.energy_curve:
            if not _is_finite_number(value) or value < 0.0:
                raise ValueError("TrackStructureAnalysis.energy_curve values must be finite and non-negative.")
        if not isinstance(self.analyzer_id, str) or not isinstance(self.analyzer_version, str):
            raise ValueError("TrackStructureAnalysis analyzer_id/analyzer_version must be strings.")

    def energy_at(self, seconds: float) -> float | None:
        """The nearest energy_curve sample at/around ``seconds``, or ``None`` if unavailable.

        Pure helper for a future planner comparing local energy at a
        candidate mix-out/mix-in point -- clamps to the curve's own span
        instead of extrapolating past it.
        """
        if not self.energy_curve or not self.energy_curve_hop_seconds:
            return None
        index = round(seconds / self.energy_curve_hop_seconds)
        index = max(0, min(len(self.energy_curve) - 1, index))
        return self.energy_curve[index]

    def to_cache_fields(self) -> dict[str, Any]:
        """Serialize every field except identity (track_id/source_path).

        Identity is deliberately excluded, same reasoning as
        ``TrackAnalysis.to_cache_fields``: the cache is addressed by file
        fingerprint, not track ID, so two PlaylistTracks pointing at the
        same media reuse one cache entry.
        """
        return {
            "duration_seconds": self.duration_seconds,
            "intro_end_seconds": self.intro_end_seconds,
            "outro_start_seconds": self.outro_start_seconds,
            "sections": [
                {
                    "start_seconds": section.start_seconds,
                    "end_seconds": section.end_seconds,
                    "energy": section.energy,
                    "label": section.label,
                    "confidence": section.confidence,
                }
                for section in self.sections
            ],
            "energy_curve": list(self.energy_curve),
            "energy_curve_hop_seconds": self.energy_curve_hop_seconds,
            "analyzer_id": self.analyzer_id,
            "analyzer_version": self.analyzer_version,
        }

    @classmethod
    def from_cache_fields(
        cls, track_id: str, source_path: str, fields: dict[str, Any],
    ) -> "TrackStructureAnalysis":
        """Rebuild a TrackStructureAnalysis for ``track_id`` from cached, JSON-safe fields."""
        return cls(
            track_id=track_id,
            source_path=source_path,
            duration_seconds=fields["duration_seconds"],
            intro_end_seconds=fields.get("intro_end_seconds"),
            outro_start_seconds=fields.get("outro_start_seconds"),
            sections=tuple(
                TrackSection(
                    start_seconds=section["start_seconds"],
                    end_seconds=section["end_seconds"],
                    energy=section.get("energy"),
                    label=section.get("label"),
                    confidence=section.get("confidence"),
                )
                for section in fields.get("sections", ())
            ),
            energy_curve=tuple(fields.get("energy_curve", ())),
            energy_curve_hop_seconds=fields.get("energy_curve_hop_seconds"),
            analyzer_id=fields.get("analyzer_id", ""),
            analyzer_version=fields.get("analyzer_version", ""),
        )
