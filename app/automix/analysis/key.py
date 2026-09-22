"""Musical key estimation and Camelot-wheel harmonic compatibility.

Pure, model-free: key is estimated by correlating a track's average
chroma vector against the classic Krumhansl-Schmuckler major/minor tone
profiles (Krumhansl & Kessler, 1982) -- a decades-old, well-documented MIR
technique, not a downloaded model. This keeps the "base engine always
available, no large downloads" rule (roadmap Phase 7 section 1) intact.

Key is used only as a score modifier (Phase 3/Phase 4's candidate
scoring), never as a blocker and never to drive automatic pitch-shifting
(roadmap section 3).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

_CAMELOT_MAJOR = {
    "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B", "B": "1B",
    "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B", "A#": "6B", "F": "7B",
}
_CAMELOT_MINOR = {
    "A": "8A", "E": "9A", "B": "10A", "F#": "11A", "C#": "12A", "G#": "1A",
    "D#": "2A", "A#": "3A", "F": "4A", "C": "5A", "G": "6A", "D": "7A",
}


def _rotate(profile: Sequence[float], steps: int) -> tuple[float, ...]:
    if steps == 0:
        return tuple(profile)
    return tuple(profile[-steps:]) + tuple(profile[:-steps])


def estimate_key(chroma_mean: Sequence[float]) -> tuple[str, float]:
    """Return ``("<Root> major"|"<Root> minor", confidence)`` for a 12-bin chroma vector.

    ``confidence`` is the winning profile's Pearson correlation, clipped to
    ``0.0..1.0`` -- a low value means the track's pitch content does not
    strongly resemble any single key (atonal, percussive, or noisy input),
    not that the estimator is broken.
    """
    if len(chroma_mean) != 12:
        raise ValueError("chroma_mean must have exactly 12 pitch-class bins.")
    chroma = np.asarray(chroma_mean, dtype=float)
    if np.allclose(chroma, 0.0) or not np.all(np.isfinite(chroma)):
        return f"{PITCH_CLASSES[0]} major", 0.0

    best_key = f"{PITCH_CLASSES[0]} major"
    best_score = -2.0
    for mode_name, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
        for root in range(12):
            rotated = np.asarray(_rotate(profile, root), dtype=float)
            with np.errstate(invalid="ignore"):
                correlation = np.corrcoef(chroma, rotated)[0, 1]
            score = float(correlation) if np.isfinite(correlation) else -2.0
            if score > best_score:
                best_score = score
                best_key = f"{PITCH_CLASSES[root]} {mode_name}"
    return best_key, max(0.0, min(1.0, best_score))


def key_to_camelot(key: str) -> str | None:
    """Convert ``"<Root> major"``/``"<Root> minor"`` to its Camelot wheel code."""
    try:
        root, mode = key.rsplit(" ", 1)
    except ValueError:
        return None
    table = _CAMELOT_MAJOR if mode == "major" else _CAMELOT_MINOR if mode == "minor" else None
    return table.get(root) if table is not None else None


def camelot_compatible(key_a: str, key_b: str) -> bool:
    """True if two keys mix cleanly on the Camelot wheel.

    Compatible: the same code, the same number with the other letter
    (relative major/minor), or an adjacent number with the same letter
    (a perfect-fifth neighbor on the wheel, wrapping 1<->12).
    """
    code_a, code_b = key_to_camelot(key_a), key_to_camelot(key_b)
    if code_a is None or code_b is None:
        return False
    if code_a == code_b:
        return True
    number_a, letter_a = int(code_a[:-1]), code_a[-1]
    number_b, letter_b = int(code_b[:-1]), code_b[-1]
    if number_a == number_b:
        return True
    if letter_a == letter_b:
        return abs(number_a - number_b) % 12 in (1, 11)
    return False
