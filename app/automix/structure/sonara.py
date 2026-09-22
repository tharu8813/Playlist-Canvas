"""Optional structure analyzer: Sonara (kkollsga/sonara) intro/outro/section/energy-curve.

Sonara (PyPI ``sonara``, MIT license -- verified directly from the
upstream ``LICENSE`` file at integration time; a small, ~1.9 MB Windows
wheel, Rust/PyO3 native extension with no Python ML dependency chain of
its own beyond numpy, which this project already depends on -- much
lighter than Beat This!/torch, app/automix/analysis/beat_this.py) provides
intro/outro boundaries, contiguous sections, and a time-resolved energy
curve via ``sonara.analyze_file(path, features=["structure"])``.

Like Beat This!, this stays a genuinely optional dependency: ``sonara`` is
imported lazily, inside analyze(), never at module import time -- a
missing/broken install, or an analysis failure for one track, must never
block rhythm analysis (Beat This!/Basic), AutoMix, or the app as a whole.

Unlike Beat This!, there is deliberately no fallback result for a
structure-analysis failure: a failed/unavailable structure analysis means
"no structure result" for that track, full stop -- never a substitute
value silently cached as if it were a genuine success. That is precisely
the class of bug the P1.5 fix addressed for Beat This!'s hybrid fallback
(a fallback result cached under the advanced provider's own cache
namespace, permanently shadowing recovery); a provider with no fallback
path at all cannot repeat it, and StructureAnalysisService
(app/automix/structure/service.py) only ever caches an actual analyze()
success, mirroring AnalysisService's own cache-on-success-only discipline.

Section labels are never fabricated: Sonara's own ``segments`` carry only
``start_sec``/``end_sec``/``energy``, no verse/chorus/bridge-style label,
so ``TrackSection.label`` stays ``None`` here -- only what the provider
actually reports is stored.

``intro_end_sec``/``outro_start_sec`` are heuristic structure-analysis
output, not ground truth. They are stored as-is (clamped only for
floating-point overshoot past the track's own duration, see
``_clamped_optional``) and are not used to drive any transition/candidate
decision in this phase -- a future planner may use them as a scoring
bonus anchor, never a forced cut point.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path
from typing import Callable

from app.automix.structure.models import TrackSection, TrackStructureAnalysis
from app.automix.structure.provider import StructureAnalysisCancelled
from app.models.playlist import PlaylistTrack

LOGGER = logging.getLogger(__name__)


def sonara_available() -> bool:
    """Cheap up-front availability probe -- resolves importability via
    importlib.util.find_spec without importing sonara itself, mirroring
    app/automix/analysis/registry.py's beat_this_available()."""
    return importlib.util.find_spec("sonara") is not None


def _installed_sonara_version() -> str:
    """Installed ``sonara`` distribution version, or "unavailable" --
    metadata-only (importlib.metadata), never imports the package."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("sonara")
    except PackageNotFoundError:
        return "unavailable"
    except Exception:  # noqa: BLE001 - a version string must never block construction
        return "unknown"


def _clamped_optional(value: object, duration_seconds: float) -> float | None:
    """Clamp a boundary timestamp into [0, duration] to absorb floating-point
    overshoot right at the track's own end; ``None`` passes through unchanged."""
    if value is None:
        return None
    return max(0.0, min(float(value), duration_seconds))


class SonaraStructureProvider:
    """StructureAnalysisProvider backed by the optional ``sonara`` package."""

    provider_id = "sonara_structure"
    version = "1"
    """This implementation's version -- see __init__, which folds in the
    installed sonara package version into the actual per-instance cache
    identity (self.version); each analyze() result additionally folds in
    Sonara's own result schema_version (analyzer_version), since that can
    change independently of the pip package version."""

    def __init__(self, *, sample_rate: int | None = None) -> None:
        self._sample_rate = sample_rate
        # Shadows the class attribute above with the full cache identity
        # for this instance. importlib.metadata reads installed-package
        # metadata without importing/executing the package, so this never
        # eagerly loads sonara just to compute a version string.
        self.version = f"{SonaraStructureProvider.version}+pkg{_installed_sonara_version()}"

    def analyze(
        self,
        track: PlaylistTrack,
        *,
        cancel_event: threading.Event,
        progress: Callable[[float, str], None] | None = None,
    ) -> TrackStructureAnalysis:
        if cancel_event.is_set():
            raise StructureAnalysisCancelled("Structure analysis cancelled before it started.")
        if progress is not None:
            progress(0.1, "Loading structure analysis engine")
        import sonara

        if progress is not None:
            progress(0.3, "Analyzing track structure")
        keyword_arguments: dict[str, object] = {"features": ["structure"]}
        if self._sample_rate is not None:
            keyword_arguments["sr"] = self._sample_rate
        result = sonara.analyze_file(str(Path(track.file_path)), **keyword_arguments)
        if cancel_event.is_set():
            # sonara.analyze_file() cannot be interrupted mid-call; this is
            # the latest point cancellation can still be honored before
            # committing to using its (already-computed) result.
            raise StructureAnalysisCancelled("Structure analysis cancelled after analyze_file().")
        if progress is not None:
            progress(0.9, "Validating structure result")

        duration_seconds = float(result.get("duration_sec") or track.duration_seconds)
        schema_version = result.get("provenance", {}).get("schema_version")
        analyzer_version = f"{self.version}+schema{schema_version}"

        sections = tuple(
            TrackSection(
                start_seconds=float(section["start_sec"]),
                end_seconds=float(section["end_sec"]),
                energy=(float(section["energy"]) if section.get("energy") is not None else None),
            )
            for section in (result.get("segments") or ())
        )
        energy_curve = tuple(float(value) for value in (result.get("energy_curve") or ()))
        hop_seconds = result.get("energy_curve_hop_sec")

        # Constructing TrackStructureAnalysis validates everything above
        # (sorted/non-overlapping sections, in-range boundaries, finite
        # energy values, ...) -- a malformed Sonara result raises ValueError
        # here rather than reaching the planner, exactly like every other
        # analyze() failure this method can raise: caught and isolated by
        # StructureAnalysisService per track, never propagated as a
        # cached "success".
        return TrackStructureAnalysis(
            track_id=track.id,
            source_path=track.file_path,
            duration_seconds=duration_seconds,
            intro_end_seconds=_clamped_optional(result.get("intro_end_sec"), duration_seconds),
            outro_start_seconds=_clamped_optional(result.get("outro_start_sec"), duration_seconds),
            sections=sections,
            energy_curve=energy_curve,
            energy_curve_hop_seconds=(float(hop_seconds) if hop_seconds is not None else None),
            analyzer_id=self.provider_id,
            analyzer_version=analyzer_version,
        )
