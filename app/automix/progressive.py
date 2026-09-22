"""Progressive AutoMix for Preview: apply transitions as analysis completes.

Pure policy, no Qt, no FFmpeg -- the Qt shell lives in
``app.controllers.progressive_automix_controller``.

Why a partial plan is safe to play: ``compile_automix`` places tracks
strictly left to right, and a pair whose analysis is missing falls back to
plain sequential adjacency. Compiling the *full* track list with analyses for
only the analyzed prefix therefore yields "AutoMix up to the frontier,
sequential after it", and every transition it plans is already exactly the
one the final, fully analyzed plan will contain -- later analyses only
append, they never move an earlier transition. The one thing a new
transition changes is the previous clip's tail (its outgoing cue), which is
exactly where :func:`divergence_seconds` places the first difference.

Render policy (the O(n^2) guard): analysis and planning react to every
event, rendering does not. A partial mix is rendered only when the listener
is about to reach a junction whose AutoMix audio is not rendered yet, after a
short debounce so bursts of analysis events collapse into one render, and
never while another render is running. Once analysis is complete the partial
path stops and the unmodified export pipeline renders the final mix.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import zip_longest

from app.automix.models import TrackAnalysis
from app.automix.planner import compile_automix
from app.automix.settings import AutoMixTransitionSettings
from app.automix.structure.models import TrackStructureAnalysis
from app.models.playlist import PlaylistTrack
from app.timeline.render_plan import AudioRenderPlan, CompiledRenderPlan

URGENT_HORIZON_SECONDS = 60.0
"""Render as soon as an unrendered transition starts within this much of the playhead."""
RENDER_DEBOUNCE_SECONDS = 0.5
"""Wait this long after the latest analysis event before rendering, to coalesce bursts."""
RENDER_MAX_DELAY_SECONDS = 2.0
"""...but never delay an urgent render longer than this after the first pending event."""
SWAP_MARGIN_SECONDS = 2.0
"""While playing, a swap must land this far before any change point or transition."""
PREVIEW_LOUDNESS_TARGET_LUFS = -16.0
PREVIEW_PEAK_CEILING_DBFS = -1.5
"""Same targets as the export loudnorm pass, so partial -> final swaps stay level."""


class ProgressiveAnalysis:
    """Which tracks have finished rhythm (and, when enabled, structure) analysis."""

    def __init__(self, tracks: Sequence[PlaylistTrack], *, structure_enabled: bool) -> None:
        self.track_ids = tuple(track.id for track in tracks)
        self.structure_enabled = structure_enabled
        self.rhythm: dict[str, TrackAnalysis] = {}
        self.structures: dict[str, TrackStructureAnalysis] = {}
        self._rhythm_done: set[str] = set()
        self._structure_done: set[str] = set()

    def record_rhythm(self, track_id: str, analysis: TrackAnalysis | None) -> None:
        self._rhythm_done.add(track_id)
        if analysis is not None:
            self.rhythm[track_id] = analysis

    def record_structure(self, track_id: str, structure: TrackStructureAnalysis | None) -> None:
        self._structure_done.add(track_id)
        if structure is not None:
            self.structures[track_id] = structure

    def _done(self, track_id: str) -> bool:
        return track_id in self._rhythm_done and (
            not self.structure_enabled or track_id in self._structure_done
        )

    def completed_count(self) -> int:
        return sum(1 for track_id in self.track_ids if self._done(track_id))

    def frontier(self) -> int:
        """How many leading tracks are fully analyzed (the prefix a plan can use)."""
        count = 0
        for track_id in self.track_ids:
            if not self._done(track_id):
                break
            count += 1
        return count

    def complete(self) -> bool:
        return self.frontier() == len(self.track_ids)


def partial_plan(
    tracks: Sequence[PlaylistTrack], state: ProgressiveAnalysis, settings: AutoMixTransitionSettings,
) -> CompiledRenderPlan:
    """AutoMix through the analyzed prefix, sequential after it (see module docstring)."""
    prefix = state.track_ids[:state.frontier()]
    return compile_automix(
        tracks,
        {track_id: state.rhythm[track_id] for track_id in prefix if track_id in state.rhythm},
        settings,
        structures={track_id: state.structures[track_id] for track_id in prefix if track_id in state.structures},
        log_diagnostics=False,
    )


def divergence_seconds(old: CompiledRenderPlan, new: CompiledRenderPlan) -> float:
    """First timeline point where the two plans' audio can differ; ``inf`` if identical.

    A clip that only changed its ``source_out`` (the outgoing clip of a newly
    planned transition) is identical up to where either version ends.
    Gain is ignored: partial mixes and the final mix share one level policy.
    """
    # Compared by what is heard, not by clip_id: the sequential compiler and
    # the AutoMix planner name clips differently for the very same placement.
    def placement(clip):
        return clip.track_id, clip.timeline_start, clip.source_in, clip.playback_rate

    points: list[float] = []
    for before, after in zip_longest(old.audio.clips, new.audio.clips):
        if before is not None and after is not None and placement(before) == placement(after) \
                and before.source_out == after.source_out:
            continue
        if before is None or after is None:
            points.append((before or after).timeline_start)
        elif placement(before) != placement(after):
            points.append(min(before.timeline_start, after.timeline_start))
        else:
            points.append(min(before.timeline_end, after.timeline_end))
        break

    def windows(plan):
        return {(t.timeline_start, t.duration, t.type, t.dsp, t.vocal_handoff) for t in plan.audio.transitions}

    points.extend(start for start, *_rest in windows(old) ^ windows(new))
    return min(points, default=math.inf)


def swap_is_safe(
    committed: CompiledRenderPlan, candidate: CompiledRenderPlan, playhead: float, playing: bool,
) -> bool:
    """Whether Preview may replace ``committed`` with ``candidate`` right now.

    Never while the listener is at/after the point where the two differ (the
    same global second would be different music), and, while playing, never
    inside -- or about to enter -- a transition: an overlap already sounding
    keeps its current audio and the new mix takes over at a solo stretch.
    """
    margin = SWAP_MARGIN_SECONDS if playing else 0.0
    if playhead + margin >= divergence_seconds(committed, candidate):
        return False
    if playing:
        for transition in (*committed.audio.transitions, *candidate.audio.transitions):
            if transition.timeline_start - margin <= playhead < transition.timeline_start + transition.duration:
                return False
    return True


def render_prefix(plan: CompiledRenderPlan, track_count: int, gain: float) -> tuple[AudioRenderPlan, float]:
    """The partial mix to render: the first ``track_count`` clips, at ``gain``.

    Returns the render plan and the timeline second its audio covers up to;
    Preview plays per-track audio after that point.
    """
    clips = plan.audio.clips[:track_count]
    clip_ids = {clip.clip_id for clip in clips}
    render = AudioRenderPlan(
        clips=tuple(replace(clip, gain=clip.gain * gain) for clip in clips),
        transitions=tuple(t for t in plan.audio.transitions if t.clip_a in clip_ids and t.clip_b in clip_ids),
    )
    return render, max((clip.timeline_end for clip in clips), default=0.0)


def preview_gain(measurements: Mapping[str, tuple[float, float]], durations: Mapping[str, float]) -> float:
    """Linear gain approximating the export loudnorm pass for a partial mix.

    ``measurements`` maps track id -> (integrated LUFS, sample peak dBFS).
    Integrated loudness is combined energy-weighted by duration, then capped
    so the loudest peak stays under the export ceiling -- what linear-mode
    loudnorm would apply to the whole mix. 1.0 when nothing is measurable.
    """
    energy = total = 0.0
    peak = -math.inf
    for track_id, (lufs, peak_dbfs) in measurements.items():
        if not math.isfinite(lufs):
            continue
        seconds = max(durations.get(track_id, 0.0), 1e-3)
        energy += seconds * 10 ** (lufs / 10)
        total += seconds
        peak = max(peak, peak_dbfs)
    if total <= 0.0 or energy <= 0.0:
        return 1.0
    gain_db = PREVIEW_LOUDNESS_TARGET_LUFS - 10 * math.log10(energy / total)
    if math.isfinite(peak):
        gain_db = min(gain_db, PREVIEW_PEAK_CEILING_DBFS - peak)
    return 10 ** (gain_db / 20)


class Action(str, Enum):
    NONE = "none"
    RENDER = "render"
    FINAL = "final"


@dataclass
class RenderScheduler:
    """Decides when to render a partial mix, when to finalize, and when to wait.

    Fed by the controller: plan updates (every analysis event), playhead
    reports from Preview, and render start/finish. ``decide(now)`` is the
    only output; call it again after ``wake_after(now)`` seconds.
    """

    attached: bool = False
    playhead: float = 0.0
    playing: bool = False
    latest: CompiledRenderPlan | None = None
    latest_frontier: int = 0
    rendered: CompiledRenderPlan | None = None
    rendered_frontier: int = 0
    rendering: bool = False
    analysis_complete: bool = False
    final_started: bool = False
    _first_pending: float | None = field(default=None, repr=False)
    _last_update: float = field(default=0.0, repr=False)

    def plan_updated(self, plan: CompiledRenderPlan, frontier: int, now: float) -> None:
        self.latest, self.latest_frontier = plan, frontier
        if self._first_pending is None:
            self._first_pending = now
        self._last_update = now

    def playhead_changed(self, seconds: float, playing: bool) -> None:
        self.attached = True
        self.playhead, self.playing = seconds, playing

    def render_started(self, *, final: bool = False) -> None:
        """A render of ``latest`` (or, with ``final``, the export pipeline) began."""
        self.rendering = True
        self._first_pending = None
        if final:
            self.final_started = True
        else:
            self.rendered, self.rendered_frontier = self.latest, self.latest_frontier

    def render_finished(self) -> None:
        self.rendering = False

    def _unrendered_change(self) -> float:
        """Timeline second of the first planned-but-unrendered difference (inf if none)."""
        if self.latest is None or self.latest_frontier <= self.rendered_frontier:
            return math.inf
        if self.rendered is None:
            transitions = self.latest.audio.transitions
            return min((t.timeline_start for t in transitions), default=math.inf)
        return divergence_seconds(self.rendered, self.latest)

    def _urgent(self) -> bool:
        change = self._unrendered_change()
        if not math.isfinite(change) or change <= self.playhead:
            # Nothing new, or the listener already passed it: a render could not
            # be swapped in without moving the music under the playhead.
            return False
        if change - self.playhead <= URGENT_HORIZON_SECONDS:
            return True
        # The listener's very next junction is the unrendered one.
        rendered = self.rendered.audio.transitions if self.rendered is not None else ()
        return not any(self.playhead < t.timeline_start + t.duration and t.timeline_start < change for t in rendered)

    def decide(self, now: float) -> Action:
        if self.rendering or self.final_started:
            return Action.NONE
        if self.analysis_complete:
            return Action.FINAL
        if not self.attached or not self._urgent():
            return Action.NONE
        settled = now - self._last_update >= RENDER_DEBOUNCE_SECONDS
        overdue = self._first_pending is not None and now - self._first_pending >= RENDER_MAX_DELAY_SECONDS
        return Action.RENDER if settled or overdue else Action.NONE

    def wake_after(self, now: float) -> float | None:
        """Seconds until ``decide`` could change on its own (debounce expiry), else None."""
        if self.rendering or self.final_started or self.analysis_complete or not self.attached:
            return None
        if not self._urgent():
            return None
        waits = [RENDER_DEBOUNCE_SECONDS - (now - self._last_update)]
        if self._first_pending is not None:
            waits.append(RENDER_MAX_DELAY_SECONDS - (now - self._first_pending))
        return max(0.0, min(waits))
