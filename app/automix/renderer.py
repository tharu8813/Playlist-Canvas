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
chain instead of one delay-position per input.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.timeline.models import TransitionType
from app.timeline.render_plan import AudioRenderClip, AudioRenderPlan, AudioRenderTransition
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
here -- see build_filter_graph()."""


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
) -> tuple[str, str]:
    """Build the filter_complex graph for ``clips``, in input order.

    Returns ``(filter_complex, output_label)``. Input ``i`` in the
    eventual FFmpeg command must be ``clips[i]``'s own source file, in the
    same order. Pure and FFmpeg-free: safe to unit test without a real
    executable.
    """
    if not clips:
        raise AutoMixRenderError("Cannot render an AudioRenderPlan with no clips.")

    transition_by_pair = {(t.clip_a, t.clip_b): t for t in transitions}
    filters: list[str] = []
    labels = [f"c{index}" for index in range(len(clips))]
    for index, clip in enumerate(clips):
        filters.append(_clip_filter_chain(index, clip, labels[index]))

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
        if transition is not None and transition.duration > 0.0:
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


def _clip_filter_chain(index: int, clip: AudioRenderClip, label: str) -> str:
    parts = [
        f"[{index}:a]atrim=start={clip.source_in:.6f}:end={clip.source_out:.6f}",
        "asetpts=PTS-STARTPTS",
    ]
    parts.extend(_atempo_filters(clip.playback_rate))
    if abs(clip.gain - 1.0) > 1e-9:
        parts.append(f"volume={clip.gain:.6f}")
    parts.append(f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo")
    return ",".join(parts) + f"[{label}]"


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
    ) -> PreparedAudio:
        """Render ``plan`` using ``track_paths`` (track_id -> source file) for its clips."""
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
        filter_complex, output_label = build_filter_graph(clips, plan.transitions)

        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = output_directory / "automix_mix.m4a"
        expected_duration = max(clip.timeline_end for clip in clips)

        arguments = [str(self.ffmpeg_executable), "-hide_banner", "-loglevel", "error", "-nostdin"]
        for clip in clips:
            arguments.extend(["-i", str(Path(track_paths[clip.track_id]))])
        arguments.extend([
            "-filter_complex", filter_complex,
            "-map", f"[{output_label}]",
            "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-y", str(output_path),
        ])

        report("Rendering transitions", 0.1, "Rendering AutoMix transitions")

        def on_progress_line(seconds: float) -> None:
            fraction = 0.1 + 0.75 * min(1.0, seconds / max(0.01, expected_duration))
            report("Combining mix", fraction, f"Combining mix {seconds:.1f}s / {expected_duration:.1f}s")

        try:
            self._run(arguments, cancel_event, on_progress_line)
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
