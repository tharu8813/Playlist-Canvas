"""AutoMix audio rendering: AudioRenderPlan -> one real mixed audio file.

Architecture boundary (roadmap Phase 5 section 1): this is a standalone
audio pipeline, deliberately not wired into FFmpegRenderer.render()'s
existing legacy normalize/silence/concat path. That path is untouched by
this module and keeps producing byte-identical output for every existing
(non-AutoMix) export -- "regression equivalence matters more than
architectural purity" (section 3). Wiring an enabled/disabled switch into
the production export button belongs to Phase 6, where the settings/UI
toggle that would drive the choice actually lives.

Filter graph strategy (section 7): rather than one global adelay+amix
graph, each clip is trimmed/rate-adjusted/gained in isolation and the
result is folded left-to-right with the SAME operation the legacy
sequential pipeline already uses at each junction -- concat for plain
adjacency, a silence-padded concat for an explicit gap, or FFmpeg's
``acrossfade`` filter for a planned transition. acrossfade's own
``duration`` parameter reproduces a transition's overlap directly, so no
manual global-timeline delay bookkeeping is needed at all. This keeps the
graph readable and lets it scale to many tracks as one filter_complex
chain instead of one delay-position per input. A transition carrying a
DSP style (``AudioRenderTransition.dsp``, chosen by the planner; BEAT_MATCH
defaults to bass swap) adds band envelopes and a window limiter on top of
the same acrossfade overlap (see BAND_ENVELOPES below); a transition
without one renders exactly as before.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.timeline.models import TransitionType
from app.timeline.render_plan import AudioRenderClip, AudioRenderPlan, AudioRenderTransition, TransitionDsp
from app.utils.subprocess_utils import hidden_process_kwargs

LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 48000
DURATION_TOLERANCE_SECONDS = 0.15
"""How far the rendered file's real duration may drift from the plan's
duration before it is treated as a render failure (section 9) rather than
silently muxed with -shortest."""

_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0
_GAP_EPSILON = 1e-6

_CURVE_BY_TRANSITION_TYPE = {
    TransitionType.EQUAL_POWER: "qsin",
    TransitionType.BEAT_MATCH: "qsin",
    TransitionType.CROSSFADE: "tri",
}
"""qsin (quarter-sine) is FFmpeg's documented equal-power-style acrossfade
curve; tri (linear/triangular) is a plain crossfade. CUT never reaches
here -- see build_filter_graph(). BEAT_MATCH only uses its qsin entry as
the fallback when the bass-swap DSP below is unavailable."""

# Band DSP for styled transitions (BASS_SWAP / VOCAL_SAFE_EQ / FILTER_BLEND). Each participating clip is split
# into three Linkwitz-Riley bands by ``acrossover`` over its WHOLE length (a
# DJ mixer's EQ is always in circuit): LR bands sum back to an allpass, so a
# clip with every band at unity keeps a flat magnitude response, and running
# the filter continuously avoids the waveform jump that splicing raw audio
# against its phase-shifted crossover output would cause at a window edge.
# Each band then gets its own fade envelope, so the fold only has to sum the
# overlap (acrossfade curve "nofade") -- keeping acrossfade's exact-duration
# overlap semantics unchanged from every other transition type.
#
# Deliberate Phase 1 trade-off: the crossover covers the whole clip, so a clip
# that is BEAT_MATCH on one side and CROSSFADE/CUT/gap on the other still plays
# the crossover-recombined signal end to end -- including across that other
# junction. Magnitude response stays flat (+/-0.5 dB measured), but the phase is
# altered. Restricting the crossover to the transition window instead would
# splice raw audio against its phase-shifted recombination and click.
LOW_CROSSOVER_HZ = 200
"""Below ~200 Hz sit kick fundamentals and bass lines, the part that muddies
when two tracks play at once; above it are kick click and bass harmonics."""
HIGH_CROSSOVER_HZ = 2500
"""Splits body/vocal mids from presence/hats so VOCAL_SAFE_EQ and
FILTER_BLEND can move them separately."""
_CROSSOVER_ORDER = "4th"
_FULL_WINDOW = (0.0, 1.0)
BAND_ENVELOPES: Mapping[TransitionDsp, Mapping[str, tuple[tuple[float, float], tuple[float, float]]]] = {
    # Bass lines barely overlap; mids/highs crossfade like qsin.
    TransitionDsp.BASS_SWAP: {
        "low": ((0.35, 0.60), (0.35, 0.60)),
        "mid": (_FULL_WINDOW, _FULL_WINDOW),
        "high": (_FULL_WINDOW, _FULL_WINDOW),
    },
    # Bass swap plus a mid (vocal/chord) swap just after it, so two vocal
    # lines -- or two clashing keys -- share only a short handoff.
    TransitionDsp.VOCAL_SAFE_EQ: {
        "low": ((0.35, 0.60), (0.35, 0.60)),
        "mid": ((0.40, 0.65), (0.40, 0.65)),
        "high": (_FULL_WINDOW, _FULL_WINDOW),
    },
    # A 3-band stand-in for a filter sweep: the incoming track arrives
    # highs-first and the outgoing one keeps its highs longest. The lows swap
    # in a short, slightly staggered equal-power handoff around the middle
    # (outgoing leads by 4%): two kicks share only ~0.35 s at <= -7 dB each
    # (8 s window), and the bass never drops out. Phase 2's (0-45%)/(55-100%)
    # lows left a bass-less gap: a -7 dB level hole on bass-heavy material,
    # vs ~-2.2 dB now (DSP Phase 2.1 envelope comparison).
    TransitionDsp.FILTER_BLEND: {
        "low": ((0.42, 0.54), (0.46, 0.58)),
        "mid": ((0.15, 0.75), (0.25, 0.85)),
        "high": ((0.35, 1.0), (0.0, 0.65)),
    },
}
"""Per style and band: ((outgoing start, end), (incoming start, end)) of each
qsin fade as normalized transition progress. Outgoing holds unity until its
start and is silent after its end; incoming is silent until its start and at
unity after its end. SHORT_FADE is absent: it needs no band split."""
VOCAL_MID_SWAP_WIDTH = 0.25
"""VOCAL_SAFE_EQ's mid (voice) swap length when the planner moves it to a
``vocal_handoff`` center; the default (0.40-0.65) is the same width."""
_BANDS = ("low", "mid", "high")
_BAND_FADE_CURVE = "qsin"

# FILTER_SWEEP: a continuous, DJ-style highpass swap instead of FILTER_BLEND's
# three fixed bands. One 2-pole highpass per clip, its cutoff moved every
# SWEEP_STEP_SECONDS by asendcmd (exponential in frequency), plus qsin level
# fades; the overlap is summed and limited like the band styles. Measured on
# the bundled FFmpeg 9 (Phase 04): direct-form-I biquad updated every 10 ms
# leaves < -78 dB of zipper residual on a 440 Hz tone (floor -79 dB); the
# svf/tdii/lattice forms were worse at the same step. The outgoing track loses
# its lows first and its highs last; the incoming one arrives highs-first
# (same intent as FILTER_BLEND's envelopes, without the band steps).
SWEEP_FLOOR_HZ = 10.0
"""Resting cutoff: -0.02 dB at 40 Hz, i.e. transparent."""
SWEEP_CEILING_HZ = 4000.0
SWEEP_STEP_SECONDS = 0.01
SWEEP_SHAPES = {
    # side: ((sweep start, end), (level fade start, end)) as normalized transition progress
    "out": ((0.2, 0.9), (0.6, 1.0)),
    "in": ((0.1, 0.8), (0.0, 0.4)),
}
DSP_PEAK_LIMIT = 0.97
"""Summing two enveloped tracks can overshoot full scale, and the pcm_s16le
intermediate would hard-clip it irrecoverably. A lookahead limiter runs on
the transition window only; below this level it is bit-transparent."""
INLINE_FILTER_GRAPH_LIMIT = 16000
"""Longer graphs go to FFmpeg as a file (``-/filter_complex``, FFmpeg 7+);
shorter ones stay inline so typical playlists run exactly as before."""
_DSP_REQUIRED_FILTERS = frozenset({"acrossover", "afade", "amix", "alimiter", "asplit", "acrossfade"})
"""FILTER_SWEEP's asendcmd/asetnsamples/highpass are not required here: a build
lacking them fails that render, which retries with legacy crossfades."""


class AutoMixRenderError(RuntimeError):
    """A controlled AutoMix render failure: bad plan, missing media, FFmpeg error."""


class AutoMixRenderCancelled(AutoMixRenderError):
    """Raised when ``cancel_event`` fires during rendering."""


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    """One rendered AutoMix mix, ready to hand to the existing video mux."""

    path: Path
    duration_seconds: float


def build_filter_graph(
    clips: Sequence[AudioRenderClip], transitions: Sequence[AudioRenderTransition],
    *, transition_dsp: bool = True, ramp_filter: str | None = "rubberband",
) -> tuple[str, str]:
    """Build the filter_complex graph for ``clips``, in input order.

    Returns ``(filter_complex, output_label)``. Input ``i`` in the
    eventual FFmpeg command must be ``clips[i]``'s own source file, in the
    same order. Pure and FFmpeg-free: safe to unit test without a real
    executable. ``transition_dsp=False`` renders every DSP-styled transition
    as its type's plain legacy acrossfade (BEAT_MATCH: qsin) -- the fallback
    when the DSP filters are unavailable. Timing is identical either way.
    ``ramp_filter`` is how tempo ramps stretch (see _clip_filter_chain).
    """
    if not clips:
        raise AutoMixRenderError("Cannot render an AudioRenderPlan with no clips.")

    transition_by_pair = {(t.clip_a, t.clip_b): t for t in transitions}
    # styles[i] is the DSP style of the junction between clips[i] and clips[i + 1], if any.
    styles: list[TransitionDsp | None] = []
    for index in range(1, len(clips)):
        transition = transition_by_pair.get((clips[index - 1].clip_id, clips[index].clip_id))
        styles.append(transition_dsp_style(transition) if transition_dsp and transition is not None else None)

    def band_side(index: int) -> BandSide | None:
        if not 0 <= index < len(styles) or styles[index] not in BAND_ENVELOPES:
            return None
        transition = transition_by_pair[(clips[index].clip_id, clips[index + 1].clip_id)]
        return styles[index], transition.duration, transition.vocal_handoff

    def sweep_side(index: int) -> float | None:
        if not 0 <= index < len(styles) or styles[index] is not TransitionDsp.FILTER_SWEEP:
            return None
        return transition_by_pair[(clips[index].clip_id, clips[index + 1].clip_id)].duration

    filters: list[str] = []
    labels = [f"c{index}" for index in range(len(clips))]
    for index, clip in enumerate(clips):
        incoming, outgoing = band_side(index - 1), band_side(index)
        sweep_in, sweep_out = sweep_side(index - 1), sweep_side(index)
        if incoming is None and outgoing is None and sweep_in is None and sweep_out is None:
            filters.append(_clip_filter_chain(index, clip, labels[index], ramp_filter=ramp_filter))
            continue
        current = f"{labels[index]}pre"
        filters.append(_clip_filter_chain(index, clip, current, ramp_filter=ramp_filter))
        if sweep_in is not None or sweep_out is not None:
            swept = f"{labels[index]}swept"
            filters.append(_sweep_filter(index, current, swept, clip.duration, sweep_in, sweep_out))
            current = swept
        if incoming is None and outgoing is None:
            filters.append(f"[{current}]anull[{labels[index]}]")
        else:
            filters.extend(_band_filters(current, labels[index], clip.duration, incoming, outgoing))

    running_label = labels[0]
    if clips[0].timeline_start > _GAP_EPSILON:
        filters.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo:d={clips[0].timeline_start:.6f}[lead]")
        filters.append(f"[lead][{running_label}]concat=n=2:v=0:a=1[leading]")
        running_label = "leading"
    silence_count = 0
    for index in range(1, len(clips)):
        previous_clip, clip = clips[index - 1], clips[index]
        transition = transition_by_pair.get((previous_clip.clip_id, clip.clip_id))
        next_label = f"m{index}"
        style = styles[index - 1]
        if style is not None:
            filters.extend(_limited_overlap_filters(running_label, labels[index], next_label, transition, style))
        elif transition is not None and transition.duration > 0.0:
            curve = _CURVE_BY_TRANSITION_TYPE.get(transition.type, "tri")
            filters.append(
                f"[{running_label}][{labels[index]}]acrossfade="
                f"d={transition.duration:.6f}:curve1={curve}:curve2={curve}[{next_label}]"
            )
        else:
            gap = clip.timeline_start - previous_clip.timeline_end
            if gap > _GAP_EPSILON:
                silence_label = f"sil{silence_count}"
                silence_count += 1
                filters.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo:d={gap:.6f}[{silence_label}]")
                bridged_label = f"g{index}"
                filters.append(f"[{running_label}][{silence_label}]concat=n=2:v=0:a=1[{bridged_label}]")
                filters.append(f"[{bridged_label}][{labels[index]}]concat=n=2:v=0:a=1[{next_label}]")
            else:
                filters.append(f"[{running_label}][{labels[index]}]concat=n=2:v=0:a=1[{next_label}]")
        running_label = next_label
    return ";".join(filters), running_label


def _clip_filter_chain(index: int, clip: AudioRenderClip, label: str, *, ramp_filter: str | None = "rubberband") -> str:
    """One clip, trimmed/stretched/gained to exactly its planned placement.

    A TempoRamp renders with ``ramp_filter`` (a time-stretcher taking timed
    ``tempo`` commands); ``None`` renders its rate steps as separate,
    sample-pinned segments instead (same timing, a stretcher restart per step).
    """
    if clip.tempo_ramp is not None and ramp_filter is None:
        return _segmented_clip_filters(index, clip, label)
    parts = [
        f"[{index}:a]atrim=start={clip.source_in:.6f}:end={clip.source_out:.6f}",
        "asetpts=PTS-STARTPTS",
    ]
    if clip.tempo_ramp is None:
        parts.extend(_atempo_filters(clip.playback_rate))
    else:
        parts.extend(_tempo_ramp_filters(index, clip, ramp_filter))
    if abs(clip.gain - 1.0) > 1e-9:
        parts.append(f"volume={clip.gain:.6f}")
    parts.append(f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo")
    if clip.tempo_ramp is not None:
        # Overlaps align on the clip's END (acrossfade), so pin its length to
        # the plan: the stretcher may leave it a frame short or long.
        samples = round(clip.duration * SAMPLE_RATE)
        parts.append(f"apad=whole_len={samples},atrim=end_sample={samples}")
    return ",".join(parts) + f"[{label}]"


RAMP_COMMAND_FRAME_SAMPLES = 512
"""asendcmd fires per input frame: small frames land each tempo step within ~11 ms of its source second."""


def _tempo_ramp_filters(index: int, clip: AudioRenderClip, ramp_filter: str) -> list[str]:
    """One continuous stretcher whose tempo follows ``clip.rate_segments()`` via timed commands.

    rubberband, not atempo: measured on FFmpeg 9, every atempo ``tempo``
    command dropped ~21 ms of input, so a 32-step ramp played its beats
    690 ms early; rubberband stayed within 3 ms of the plan over the same
    ramp. Command times are clip-local *source* seconds, the timestamps
    asendcmd sees before the stretcher.
    """
    name = f"{ramp_filter}@ramp{index}"
    segments = clip.rate_segments()
    commands = ";".join(f"{start - clip.source_in:.6f} {name} tempo {rate:.6f}" for start, _end, rate in segments[1:])
    return [
        f"asetnsamples=n={RAMP_COMMAND_FRAME_SAMPLES}:p=0",
        f"asendcmd=c='{commands}'",
        f"{name}=tempo={segments[0][2]:.6f}",
    ]


def _segmented_clip_filters(index: int, clip: AudioRenderClip, label: str) -> str:
    """Fallback ramp without a command-driven stretcher: one atempo per rate step.

    Each step is pinned to the sample count the plan gives it (boundaries
    rounded once, on the cumulative timeline), so timing never accumulates
    error; the cost is a stretcher restart at each step.
    # ponytail: measured 15-18 ms early inside a long constant-rate step
    # (atempo start-up), vs <= 3 ms with rubberband; compensate if this path matters.
    """
    segments = clip.rate_segments()
    names = [f"r{index}s{k}" for k in range(len(segments))]
    filters = [f"[{index}:a]asplit={len(segments)}" + "".join(f"[{name}]" for name in names)]
    edge, elapsed = 0, 0.0
    for name, (start, end, rate) in zip(names, segments):
        elapsed += (end - start) / rate
        samples = round(elapsed * SAMPLE_RATE) - edge
        edge += samples
        filters.append(",".join([
            f"[{name}]atrim=start={start:.6f}:end={end:.6f}", "asetpts=PTS-STARTPTS",
            *_atempo_filters(rate), f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo",
            f"apad=whole_len={samples}", f"atrim=end_sample={samples}",
        ]) + f"[{name}p]")
    joined = "".join(f"[{name}p]" for name in names) + f"concat=n={len(segments)}:v=0:a=1"
    gain = f",volume={clip.gain:.6f}" if abs(clip.gain - 1.0) > 1e-9 else ""
    filters.append(f"{joined}{gain}[{label}]")
    return ";".join(filters)


def transition_dsp_style(transition: AudioRenderTransition) -> TransitionDsp | None:
    """The DSP style this transition renders with, ``None`` for the type's legacy acrossfade.

    An explicit ``dsp`` from the plan wins. Without one, BEAT_MATCH keeps its
    Phase 1 bass swap and every other type its legacy curve.
    """
    if transition.duration <= 0.0:
        return None
    if transition.dsp is not None:
        return transition.dsp
    return TransitionDsp.BASS_SWAP if transition.type == TransitionType.BEAT_MATCH else None


BandSide = tuple[TransitionDsp, float, float | None]
"""(band style, transition duration, vocal handoff) for one side of a clip."""


def _band_envelope(style: TransitionDsp, band: str, vocal_handoff: float | None):
    """BAND_ENVELOPES entry, with VOCAL_SAFE_EQ's mid swap moved to the planned handoff."""
    if style is TransitionDsp.VOCAL_SAFE_EQ and band == "mid" and vocal_handoff is not None:
        window = (vocal_handoff - VOCAL_MID_SWAP_WIDTH / 2, vocal_handoff + VOCAL_MID_SWAP_WIDTH / 2)
        return window, window
    return BAND_ENVELOPES[style][band]


def band_fade_windows(
    band: str, clip_duration: float, incoming: BandSide | None, outgoing: BandSide | None,
) -> list[tuple[str, float, float]]:
    """``(fade_type, start, length)`` afade windows for one band of one clip.

    Times are clip-local timeline seconds (after atempo). The incoming
    window is the clip's first ``duration`` seconds and the outgoing one its
    last -- exactly the regions acrossfade overlaps -- so envelopes follow
    the plan's geometry without recomputing any of it. ``None`` means that
    side has no band-DSP transition.
    """
    windows: list[tuple[str, float, float]] = []
    if incoming is not None:
        style, duration, handoff = incoming
        start, end = _band_envelope(style, band, handoff)[1]
        windows.append(("in", duration * start, duration * (end - start)))
    if outgoing is not None:
        style, duration, handoff = outgoing
        start, end = _band_envelope(style, band, handoff)[0]
        window_start = clip_duration - duration
        windows.append(("out", window_start + duration * start, duration * (end - start)))
    return windows


def sweep_cutoffs(
    clip_duration: float, incoming: float | None, outgoing: float | None,
) -> tuple[float, list[tuple[float, float]]]:
    """(initial cutoff Hz, [(clip-local second, cutoff Hz)]) for one clip's sweep highpass.

    ``incoming``/``outgoing`` are that side's FILTER_SWEEP transition
    duration, ``None`` when that side has no sweep. Deterministic.
    """
    points: list[tuple[float, float]] = []

    def sweep(window_start: float, duration: float, side: str, start_hz: float, end_hz: float) -> None:
        (begin, end), _fade = SWEEP_SHAPES[side]
        first, last = window_start + duration * begin, window_start + duration * end
        steps = max(1, round((last - first) / SWEEP_STEP_SECONDS))
        points.extend(
            (first + (last - first) * step / steps, start_hz * (end_hz / start_hz) ** (step / steps))
            for step in range(steps + 1)
        )

    if incoming is not None:
        sweep(0.0, incoming, "in", SWEEP_CEILING_HZ, SWEEP_FLOOR_HZ)
    if outgoing is not None:
        sweep(clip_duration - outgoing, outgoing, "out", SWEEP_FLOOR_HZ, SWEEP_CEILING_HZ)
    points.sort()  # asendcmd wants time order; only a clip shorter than both windows interleaves
    return (SWEEP_CEILING_HZ if incoming is not None else SWEEP_FLOOR_HZ), points


def _sweep_filter(
    index: int, source: str, output: str, clip_duration: float, incoming: float | None, outgoing: float | None,
) -> str:
    """One clip's sweep: moving highpass (asendcmd) plus the sides' qsin level fades."""
    name = f"highpass@sweep{index}"
    initial, points = sweep_cutoffs(clip_duration, incoming, outgoing)
    commands = ";".join(f"{seconds:.4f} {name} f {cutoff:.2f}" for seconds, cutoff in points)
    parts = [
        # Small frames: a biquad takes new coefficients per frame. p=0: never pad (exact duration).
        f"asetnsamples=n={round(SWEEP_STEP_SECONDS * SAMPLE_RATE)}:p=0",
        f"asendcmd=c='{commands}'",
        f"{name}=f={initial:.2f}:r=f64",
    ]
    if incoming is not None:
        (_sweep, (begin, end)) = SWEEP_SHAPES["in"]
        parts.append(f"afade=t=in:st={incoming * begin:.6f}:d={incoming * (end - begin):.6f}:curve=qsin")
    if outgoing is not None:
        (_sweep, (begin, end)) = SWEEP_SHAPES["out"]
        start = clip_duration - outgoing + outgoing * begin
        parts.append(f"afade=t=out:st={start:.6f}:d={outgoing * (end - begin):.6f}:curve=qsin")
    return f"[{source}]" + ",".join(parts) + f"[{output}]"


def _band_filters(
    source: str, output: str, clip_duration: float, incoming: BandSide | None, outgoing: BandSide | None,
) -> list[str]:
    """Split ``source`` into bands, envelope each, and sum them back into ``output``."""
    band_labels = [f"{output}{band}" for band in _BANDS]
    filters = [
        f"[{source}]acrossover=split={LOW_CROSSOVER_HZ} {HIGH_CROSSOVER_HZ}:order={_CROSSOVER_ORDER}"
        + "".join(f"[{label}]" for label in band_labels)
    ]
    for band, label in zip(_BANDS, band_labels):
        fades = [
            f"afade=t={fade_type}:st={start:.6f}:d={length:.6f}:curve={_BAND_FADE_CURVE}"
            for fade_type, start, length in band_fade_windows(band, clip_duration, incoming, outgoing)
        ]
        filters.append(f"[{label}]{','.join(fades) or 'anull'}[{label}e]")
    filters.append(
        "".join(f"[{label}e]" for label in band_labels)
        + f"amix=inputs={len(_BANDS)}:normalize=0[{output}]"
    )
    return filters


DROP_IN_ATTACK_SECONDS = 0.02
"""DROP_IN's incoming fade-in: just long enough not to click."""


def _limited_overlap_filters(
    running: str, incoming: str, output: str, transition: AudioRenderTransition, style: TransitionDsp,
) -> list[str]:
    """Overlap two clips as ``style`` mixes them, then limit only the overlap window.

    Band styles and the sweep carry their own envelopes (``nofade``), so
    acrossfade just sums the last/first ``duration`` seconds. SHORT_FADE is a
    full-band qsin. DROP_IN fades only the outgoing, already decaying, side:
    the incoming one is at full level after DROP_IN_ATTACK_SECONDS. The
    limiter runs on that window alone, cut at ``transition.timeline_start``
    in the running timeline, so audio outside the transition is untouched.
    """
    start = transition.timeline_start
    end = start + transition.duration
    summed = f"{output}sum"
    enveloped = style in BAND_ENVELOPES or style is TransitionDsp.FILTER_SWEEP
    curve = "nofade" if enveloped else "qsin"
    incoming_curve = "nofade" if style is TransitionDsp.DROP_IN else curve
    attack = []
    if style is TransitionDsp.DROP_IN:
        attack = [f"[{incoming}]afade=t=in:d={DROP_IN_ATTACK_SECONDS}[{output}attack]"]
        incoming = f"{output}attack"
    return [
        *attack,
        f"[{running}][{incoming}]acrossfade=d={transition.duration:.6f}:curve1={curve}:curve2={incoming_curve}"
        f"[{summed}]",
        f"[{summed}]asplit=3[{output}a][{output}b][{output}c]",
        f"[{output}a]atrim=end={start:.6f}[{output}pre]",
        f"[{output}b]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
        f"alimiter=limit={DSP_PEAK_LIMIT}:level=0:latency=1[{output}win]",
        f"[{output}c]atrim=start={end:.6f},asetpts=PTS-STARTPTS[{output}post]",
        f"[{output}pre][{output}win][{output}post]concat=n=3:v=0:a=1[{output}]",
    ]


@lru_cache(maxsize=None)
def ffmpeg_filter_names(executable: str) -> frozenset[str]:
    """Every filter ``executable`` lists (cached per path; empty if it cannot be asked)."""
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-filters"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, **hidden_process_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(parts[1] for parts in (line.split() for line in result.stdout.splitlines()) if len(parts) > 2)


def ffmpeg_supports_transition_dsp(executable: str) -> bool:
    """Whether ``executable`` has every filter the transition DSP needs."""
    return _DSP_REQUIRED_FILTERS <= ffmpeg_filter_names(executable)


def _atempo_filters(rate: float) -> list[str]:
    """Decompose ``rate`` into a chain of filters each within atempo's supported range."""
    if abs(rate - 1.0) < 1e-9:
        return []
    factors: list[float] = []
    remaining = rate
    while remaining > _ATEMPO_MAX:
        factors.append(_ATEMPO_MAX)
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        factors.append(_ATEMPO_MIN)
        remaining /= _ATEMPO_MIN
    factors.append(remaining)
    return [f"atempo={factor:.6f}" for factor in factors]


ProgressCallback = Callable[[str, float, str], None]
"""(stage, fraction 0..1, message) -> None."""


class AutoMixAudioPipeline:
    """Renders one AudioRenderPlan into one mixed audio file via FFmpeg."""

    def __init__(self, ffmpeg_executable: Path) -> None:
        self.ffmpeg_executable = Path(ffmpeg_executable)

    def render(
        self,
        plan: AudioRenderPlan,
        track_paths: Mapping[str, str],
        output_directory: Path,
        *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
        container: str = "nut",
    ) -> PreparedAudio:
        """Render ``plan`` using ``track_paths`` (track_id -> source file) for its clips.

        ``container="flac"`` writes a directly playable lossless file instead
        of the PCM/NUT intermediate -- for progressive Preview, whose partial
        mixes skip the downstream loudness/AAC stage.
        """
        cancel_event = cancel_event or threading.Event()

        def report(stage: str, fraction: float, message: str) -> None:
            if progress is not None:
                progress(stage, fraction, message)

        clips = plan.clips
        if not clips:
            raise AutoMixRenderError("Cannot render an AudioRenderPlan with no clips.")
        report("Preparing AutoMix audio", 0.0, "Preparing AutoMix audio")

        missing = [clip for clip in clips if not Path(track_paths.get(clip.track_id, "")).is_file()]
        if missing:
            raise AutoMixRenderError(f"Audio file is missing for track: {missing[0].track_id}")

        report("Preparing clips", 0.05, f"Preparing {len(clips)} clip(s)")
        use_dsp = any(transition_dsp_style(t) is not None for t in plan.transitions)
        if use_dsp and not ffmpeg_supports_transition_dsp(str(self.ffmpeg_executable)):
            LOGGER.warning("FFmpeg lacks the transition DSP filters; styled transitions use their legacy crossfade.")
            use_dsp = False

        output_directory.mkdir(parents=True, exist_ok=True)
        # PCM in a NUT container, not AAC: this file is an *intermediate*
        # that prepare_playlist_audio() (app/renderer/ffmpeg_renderer.py)
        # goes on to run through loudness normalization and its own single
        # final AAC encode. Encoding this stage to AAC too meant every
        # AutoMix/crossfade export was lossy-to-lossy double-encoded, and no
        # final bitrate the user picked could recover what was already lost
        # at this fixed 192k intermediate step. NUT+PCM matches the exact
        # format the legacy sequential path's own per-track intermediates
        # already use (see _normalize_audio in ffmpeg_renderer.py), so the
        # concat/decode step downstream needs no special-casing.
        output_path = output_directory / f"automix_mix.{container}"
        codec = ["-c:a", "flac", "-compression_level", "0"] if container == "flac" else ["-c:a", "pcm_s16le"]
        expected_duration = max(clip.timeline_end for clip in clips)

        # Tempo ramps need a stretcher that takes timed tempo commands.
        ramp_filter = "rubberband" if any(clip.tempo_ramp for clip in clips) and (
            "rubberband" in ffmpeg_filter_names(str(self.ffmpeg_executable))) else None

        def command(dsp: bool) -> list[str]:
            filter_complex, output_label = build_filter_graph(
                clips, plan.transitions, transition_dsp=dsp, ramp_filter=ramp_filter,
            )
            graph = ["-filter_complex", filter_complex]
            if len(filter_complex) > INLINE_FILTER_GRAPH_LIMIT:
                # Windows caps a command line at 32,767 characters; a band-DSP
                # graph passes that around 33 tracks, and CreateProcess then
                # failed outright, silently dropping every DSP transition.
                script = output_directory / "automix_graph.txt"
                script.write_text(filter_complex, encoding="utf-8")
                graph = ["-/filter_complex", str(script)]
            arguments = [str(self.ffmpeg_executable), "-hide_banner", "-loglevel", "error", "-nostdin"]
            for clip in clips:
                arguments.extend(["-i", str(Path(track_paths[clip.track_id]))])
            arguments.extend([
                *graph,
                "-map", f"[{output_label}]",
                *codec, "-ar", str(SAMPLE_RATE), "-ac", "2", "-f", container,
                "-progress", "pipe:1", "-nostats", "-y", str(output_path),
            ])
            return arguments

        report("Rendering transitions", 0.1, "Rendering AutoMix transitions")

        def on_progress_line(seconds: float) -> None:
            fraction = 0.1 + 0.75 * min(1.0, seconds / max(0.01, expected_duration))
            report("Combining mix", fraction, f"Combining mix {seconds:.1f}s / {expected_duration:.1f}s")

        try:
            try:
                self._run(command(use_dsp), cancel_event, on_progress_line)
            except AutoMixRenderCancelled:
                raise
            except AutoMixRenderError as error:
                if not use_dsp:
                    raise
                # Same plan, same geometry -- only the transition mixing DSP degrades.
                LOGGER.warning("Transition DSP render failed (%s); retrying with legacy crossfades.", error)
                output_path.unlink(missing_ok=True)  # never let a partial DSP file survive into the retry
                self._run(command(False), cancel_event, on_progress_line)
        except AutoMixRenderCancelled:
            output_path.unlink(missing_ok=True)
            raise

        report("Validating mixed audio", 0.9, "Validating mixed audio")
        actual_duration = self._probe_duration(output_path)
        if abs(actual_duration - expected_duration) > DURATION_TOLERANCE_SECONDS:
            output_path.unlink(missing_ok=True)
            raise AutoMixRenderError(
                f"Rendered AutoMix audio duration ({actual_duration:.3f}s) does not match "
                f"the compiled plan ({expected_duration:.3f}s)."
            )
        report("Validating mixed audio", 1.0, "AutoMix audio ready")
        return PreparedAudio(path=output_path, duration_seconds=actual_duration)

    def _run(
        self, arguments: list[str], cancel_event: threading.Event,
        on_progress_seconds: Callable[[float], None] | None,
    ) -> None:
        try:
            process = subprocess.Popen(
                arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", **hidden_process_kwargs(),
            )
        except OSError as error:
            raise AutoMixRenderError(f"Could not start FFmpeg for AutoMix rendering: {error}") from error

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def drain(stream: object, sink: list[str], parse_progress: bool) -> None:
            assert stream is not None
            for line in iter(stream.readline, ""):  # type: ignore[union-attr]
                sink.append(line)
                if parse_progress and on_progress_seconds is not None:
                    seconds = _parse_progress_seconds(line.strip())
                    if seconds is not None:
                        on_progress_seconds(seconds)

        stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_lines, True), daemon=True)
        stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_lines, False), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        cancelled = False
        try:
            while process.poll() is None:
                if cancel_event.wait(0.05):
                    cancelled = True
                    process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
        finally:
            stdout_thread.join(timeout=5.0)
            stderr_thread.join(timeout=5.0)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        if cancelled:
            raise AutoMixRenderCancelled("AutoMix rendering was cancelled.")
        if process.returncode != 0:
            message = "".join(stderr_lines).strip() or "unknown FFmpeg error"
            raise AutoMixRenderError(f"FFmpeg could not render the AutoMix mix: {message}")

    def _probe_duration(self, path: Path) -> float:
        probe = self._ffprobe_executable()
        if probe is None:
            raise AutoMixRenderError("Could not locate ffprobe next to the configured FFmpeg executable.")
        try:
            result = subprocess.run(
                [str(probe), "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                **hidden_process_kwargs(),
            )
        except OSError as error:
            raise AutoMixRenderError(f"Could not run ffprobe on the rendered AutoMix audio: {error}") from error
        try:
            return float(result.stdout.strip())
        except ValueError:
            raise AutoMixRenderError("Could not determine the rendered AutoMix audio duration.") from None

    def _ffprobe_executable(self) -> Path | None:
        sibling_name = "ffprobe.exe" if self.ffmpeg_executable.suffix.lower() == ".exe" else "ffprobe"
        sibling = self.ffmpeg_executable.with_name(sibling_name)
        return sibling if sibling.is_file() else None


def _parse_progress_seconds(line: str) -> float | None:
    if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
        try:
            return float(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time="):
        try:
            hours, minutes, seconds = line.split("=", 1)[1].split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    return None
