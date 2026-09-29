"""Phrases and peak sections: where in a song a DJ would start or leave it.

Pop and dance music moves in 8-bar phrases; a transition that starts on a
phrase boundary sounds intended, one that starts on bar 3 sounds like a
mistake. The bar grid comes from the rhythm analysis (measured downbeats
only); which downbeat starts a phrase comes from the structure analysis
(section starts), falling back to counting from the first downbeat.
"""

from __future__ import annotations

from app.automix.models import TrackAnalysis
from app.automix.structure.models import TrackSection, TrackStructureAnalysis

PHRASE_BARS = 8
PEAK_SECTION_RATIO = 0.85
"""A section at least this share of the track's loudest section's energy is a
peak (chorus/drop): no labels are measured, so energy stands in for them."""


def phrase_starts(analysis: TrackAnalysis, structure: TrackStructureAnalysis | None = None) -> tuple[float, ...]:
    """Every downbeat that starts an 8-bar phrase (``()`` without a measured bar grid).

    The phase is the one whose phrase starts land on the most structure
    section starts (within half a bar); ties and missing structure keep the
    first downbeat's phase.
    """
    downbeats = analysis.downbeats
    if analysis.beat_alignment_quality() != "reliable" or len(downbeats) < PHRASE_BARS:
        return ()
    bar = (downbeats[-1] - downbeats[0]) / (len(downbeats) - 1)
    boundaries = [section.start_seconds for section in (structure.sections if structure else ())]

    def hits(phase: int) -> int:
        return sum(any(abs(start - boundary) <= bar / 2 for boundary in boundaries)
                   for start in downbeats[phase::PHRASE_BARS])

    phase = max(range(PHRASE_BARS), key=lambda candidate: (hits(candidate), -candidate))
    return tuple(downbeats[phase::PHRASE_BARS])


def peak_sections(structure: TrackStructureAnalysis | None) -> tuple[TrackSection, ...]:
    """The loudest sections (chorus/drop stand-ins); ``()`` without section energies."""
    sections = [section for section in (structure.sections if structure else ()) if section.energy is not None]
    if not sections:
        return ()
    loudest = max(section.energy for section in sections)
    return tuple(section for section in sections if section.energy >= PEAK_SECTION_RATIO * loudest)


def in_peak(structure: TrackStructureAnalysis | None, seconds: float) -> bool | None:
    """Whether ``seconds`` falls in a peak section (None: unknown)."""
    peaks = peak_sections(structure)
    if not peaks:
        return None
    return any(section.start_seconds <= seconds < section.end_seconds for section in peaks)
