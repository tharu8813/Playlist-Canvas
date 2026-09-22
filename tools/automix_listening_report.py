"""AutoMix listening/tuning report for real music (developer tool, not shipped).

    python tools/automix_listening_report.py OUT_DIR SONG1 SONG2 [SONG3 ...] [--ffmpeg PATH] [--ab]

Analyzes the songs in the given order exactly like Export does (rhythm
provider "auto" = Beat This when installed, Sonara structure when installed,
the persistent caches), compiles the AutoMix plan, renders the whole mix to
``OUT_DIR/automix_mix.flac`` and writes ``report.csv``/``report.json``: one row
per junction with every planner number (``app.automix.diagnostics``) plus
objective window metrics (level dip/peak, low-end build-up) measured on the
rendered mix.

``--ab`` also renders each transition as a short excerpt once per DSP style
(``OUT_DIR/ab/<n>_<style>.flac``) with the same geometry, and measures each,
for side-by-side listening. Never commit the audio files or reports.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.automix.analysis.registry import create_analysis_provider  # noqa: E402
from app.automix.analysis.service import AnalysisService  # noqa: E402
from app.automix.diagnostics import rows_to_csv, rows_to_json, transition_rows, window_metrics  # noqa: E402
from app.automix.planner import compile_automix  # noqa: E402
from app.automix.renderer import AutoMixAudioPipeline  # noqa: E402
from app.automix.settings import AutoMixTransitionSettings  # noqa: E402
from app.models.playlist import PlaylistTrack  # noqa: E402
from app.renderer.ffmpeg_renderer import FFmpegRenderer  # noqa: E402
from app.timeline.models import TransitionType  # noqa: E402
from app.timeline.render_plan import AudioRenderPlan, TransitionDsp  # noqa: E402

METRIC_RATE = 22050
EXCERPT_CONTEXT_SECONDS = 8.0
AB_STYLES = ("legacy", *(style.value for style in TransitionDsp))


def probe_duration(ffmpeg: Path, path: Path) -> float:
    probe = ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe")
    result = subprocess.run(
        [str(probe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def decode_mono(ffmpeg: Path, path: Path) -> np.ndarray:
    result = subprocess.run(
        [str(ffmpeg), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(METRIC_RATE), "-f", "f32le", "pipe:1"],
        capture_output=True, check=True,
    )
    return np.frombuffer(result.stdout, dtype=np.float32)


def excerpt_plan(plan, transition, style: str) -> tuple[AudioRenderPlan, float]:
    """Just this transition plus EXCERPT_CONTEXT_SECONDS of solo audio each side, same geometry."""
    clips = {clip.clip_id: clip for clip in plan.audio.clips}
    outgoing, incoming = clips[transition.clip_a], clips[transition.clip_b]
    cue = outgoing.source_in + (transition.timeline_start - outgoing.timeline_start) * outgoing.playback_rate
    source_in = max(outgoing.source_in, cue - EXCERPT_CONTEXT_SECONDS * outgoing.playback_rate)
    lead = (cue - source_in) / outgoing.playback_rate
    outgoing = replace(outgoing, timeline_start=0.0, source_in=source_in, gain=1.0)
    incoming = replace(
        incoming, timeline_start=lead, gain=1.0,
        source_out=min(incoming.source_out,
                       incoming.source_in + (transition.duration + EXCERPT_CONTEXT_SECONDS) * incoming.playback_rate),
    )
    if style == "legacy":
        styled = replace(transition, timeline_start=lead, dsp=None,
                         type=TransitionType.EQUAL_POWER if transition.type is TransitionType.BEAT_MATCH else transition.type)
    else:
        styled = replace(transition, timeline_start=lead, dsp=TransitionDsp(style))
    return AudioRenderPlan(clips=(outgoing, incoming), transitions=(styled,)), lead


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("songs", nargs="+", type=Path)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ab", action="store_true", help="render every transition once per DSP style")
    args = parser.parse_args()
    ffmpeg = FFmpegRenderer.find_executable(args.ffmpeg)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    tracks = [
        PlaylistTrack(str(path.resolve()), path.stem, duration_seconds=probe_duration(ffmpeg, path))
        for path in args.songs
    ]
    titles = {track.id: track.title for track in tracks}
    provider = create_analysis_provider("auto", ffmpeg)
    print(f"rhythm provider: {provider.provider_id}", flush=True)
    analyses = AnalysisService(provider).analyze_tracks(tracks).analyses
    structures = {}
    from app.automix.structure.sonara import SonaraStructureProvider, sonara_available

    if sonara_available():
        from app.automix.structure.service import StructureAnalysisService

        structures = StructureAnalysisService(SonaraStructureProvider()).analyze_tracks(tracks).analyses
    print(f"structure: {'sonara' if structures else 'unavailable'}", flush=True)

    plan = compile_automix(tracks, analyses, AutoMixTransitionSettings(enabled=True), structures=structures)
    rows = transition_rows(plan, titles)
    pipeline = AutoMixAudioPipeline(ffmpeg)
    paths = {track.id: track.file_path for track in tracks}
    mix = pipeline.render(plan.audio, paths, out_dir, container="flac")
    samples = decode_mono(ffmpeg, mix.path)
    transitions = {t.timeline_start: t for t in plan.audio.transitions}
    for row in rows:
        transition = transitions.get(row["timeline_start"]) if row["duration"] else None
        if transition is None:
            continue
        row.update(window_metrics(samples, METRIC_RATE, transition.timeline_start, transition.duration))
        if args.ab:
            for style in AB_STYLES:
                excerpt, lead = excerpt_plan(plan, transition, style)
                directory = out_dir / "ab" / f"{row['index']:02d}_{style}"
                rendered = pipeline.render(excerpt, paths, directory, container="flac")
                metrics = window_metrics(decode_mono(ffmpeg, rendered.path), METRIC_RATE, lead, transition.duration)
                row.update({f"ab_{style}_{name}": value for name, value in metrics.items()})

    (out_dir / "report.csv").write_text(rows_to_csv(rows), encoding="utf-8")
    (out_dir / "report.json").write_text(rows_to_json(rows), encoding="utf-8")
    for row in rows:
        print(f"{row['index']:>2} {row['from']} -> {row['to']}: {row['type']}/{row['dsp']} "
              f"{row['duration']:.1f}s dip={row.get('level_dip_db', float('nan')):.1f}dB "
              f"peak={row.get('level_peak_db', float('nan')):.1f}dB "
              f"low={row.get('low_excess_db', float('nan')):.1f}dB")
    print(f"mix: {mix.path}\nreport: {out_dir / 'report.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
