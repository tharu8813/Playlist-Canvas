# AutoMix Final Implementation Report

Phase 8 completion report per `docs/08_PHASE8_FINAL_INTEGRATION_RELEASE_AUDIT.md`. Covers Phases 1-8 of `docs/00_AUTOMIX_IMPLEMENTATION_ROADMAP.md` plus the ad hoc UI/export-wiring work done alongside them.

## Architecture

```text
PlaylistTrack[]
    |
AnalysisService (app/automix/analysis/service.py)
    |  -- checks AnalysisCache, calls BasicAnalysisProvider for misses
TrackAnalysis[] (per track: BPM, beats, downbeats, key, energy, vocal_activity)
    |
evaluate_compatibility() + generate_candidates() (app/automix/compatibility.py, candidates.py)
    |
compile_automix() (app/automix/planner.py)
    |
CompiledRenderPlan  <- the SAME shape Sequential's compile_playlist() produces
    |  (AudioRenderPlan with overlapping clips + transitions, Presentation, Metadata)
AutoMixAudioPipeline (app/automix/renderer.py)
    |
PreparedAudio (mixed .m4a, padded to the legacy sequential duration)
    |
FFmpegRenderer.render(use_automix=True)  <- existing video/chapter/mux pipeline, unchanged
    |
Final MP4
```

`AutoMixWorkflow` (`app/automix/workflow.py`) is the single entry point a caller uses: `analyze()` and `plan()` are separate calls so a transition-setting change never re-triggers analysis, and `build()` does both. `AutoMixAnalysisController` (`app/controllers/automix_analysis_controller.py`) wraps `analyze()` in a QThread for the UI's background badge/detail display; `FFmpegRenderer._render_automix_audio_segments()` calls the same workflow synchronously inside the export worker's own background thread.

Nothing in this stack touches `app/timeline/compiler.py`'s `compile_timeline()`/`compile_playlist()` or the Sequential scheduler -- they are separate, parallel producers of the same `CompiledRenderPlan` shape, exactly as the pre-AutoMix architecture work intended.

## Features Implemented

- Versioned, atomic, file-fingerprint-keyed analysis cache (Phase 1).
- Default analyzer: FFmpeg decode + librosa BPM/beat tracking, provisional 4/4 downbeat guess, key (Krumhansl-Schmuckler), energy (RMS heuristic), lightweight vocal-activity heuristic (Phase 2, Phase 7).
- BPM compatibility (including half/double-tempo folding) and explainable, weighted transition-candidate scoring with a documented fallback ladder: BEAT_MATCH -> BEAT_ALIGNED_CROSSFADE -> FIXED_CROSSFADE -> CUT (Phase 3).
- `AutoMixPlanner`: cumulative multi-track placement, explicit-gap preservation, single-fixed-rate-per-clip policy ("favor outgoing"), shared Presentation/Metadata timing logic with Sequential via `build_presentation_and_metadata()` (Phase 4).
- Real FFmpeg audio rendering: per-clip trim/atempo/gain, `acrossfade` (qsin for equal-power/beat-match, tri for plain crossfade) or silence-padded/plain concat at every junction, duration-validated against the plan within a documented tolerance (Phase 5).
- Per-project AutoMix toggle (`ProjectSettings.automix_enabled`, backward compatible, default off), background analysis wired to playlist changes, BPM/key/energy displayed on playlist rows and in the track details dialog (Phase 6, extended by user request).
- Key/energy/vocal-aware scoring bonuses/penalties, additive and opt-in -- identical score when the data is absent (Phase 7).
- AutoMix wired into the real export audio path: `FFmpegRenderer.render(use_automix=True)` renders the AutoMix mix and pads it with silence to the legacy sequential duration, so every downstream step (video encode, chapter mux, duration validation) is untouched (Phase 8 finding + fix).

## Features Deferred

- **Video/Canvas timing integration.** AutoMix currently changes audio only. Presentation ownership switches, chapter boundaries used for on-screen elements, and Preview all still use the legacy Sequential `CompiledRenderPlan`. This was a deliberate, explicit scope decision (see "Release Blockers" below) because wiring it correctly requires changing the frame-capture pipeline (`export_controller.py`, `app/preview/export_plan.py`, `app/renderer/export_timeline.py`) in ways that cannot be verified without actually playing back a render, which this environment cannot do.
- **Preview integration.** `ExportPreviewDialog` still plays the legacy sequential audio/timing regardless of the AutoMix project setting.
- Transition preview (short pre/post-roll render of a single transition), transition list UI cards, and a fuller settings UI (bar-length/tempo-budget/style controls) -- Phase 6 was deliberately scoped to "backend + minimal UI" per user decision.
- Optional advanced beat/downbeat engine (e.g. Beat This!) and any model manager -- not justified given the added PyTorch cost; the roadmap explicitly permits deferring this.
- EQ/bass-swap and additional named transition styles beyond CUT/CROSSFADE/EQUAL_POWER/BEAT_MATCH.
- Loudness normalization in the AutoMix audio path (the legacy path normalizes each track before concatenation; `AutoMixAudioPipeline` does not yet).
- Alternative non-AutoMix transition modes (a plain seconds-configurable crossfade, or "none") requested for a future pass; not built speculatively.

## Default Analysis Engine

`librosa` (`app/automix/analysis/basic.py`, `BasicAnalysisProvider`, `provider_id="basic"`, `version="2"`). Chosen over aubio/Essentia (GPL/AGPL) for license reasons and over Beat This!/Essentia's ML models for dependency weight; see `docs/automix-phase1-dependency-evaluation.md` for the full comparison. Decoding goes through the project's own managed FFmpeg (piped PCM, no temp file), not librosa's own audioread/soundfile path.

## Optional Engines / Models

None implemented. See "Features Deferred."

## Cache

`AnalysisCache` (`app/automix/cache.py`): one JSON file per `(canonical path, size, mtime_ns, analyzer_id, analyzer_version, schema_version)` combination under `%LOCALAPPDATA%\PlaylistCanvas\automix-cache\`. A hash mismatch on any of those fields is a cache miss (no separate invalidation bookkeeping needed); malformed/invalid entries are treated as misses, never crash. Atomic writes via temp-file-then-`replace`. Not size-bounded/pruned (unlike `PreviewProxyCache`) -- acceptable for JSON-only entries but worth revisiting if analysis JSON size grows (e.g. very long `beats` arrays for very long tracks).

## Candidate / Scoring Rules

Weighted, additive, documented in `app/automix/candidates.py`'s module docstring: BPM confidence (0.35), downbeat confidence for BEAT_MATCH only (0.15), tempo-shift-vs-budget (0.25), requested-bar-length match (0.15), cue proximity (0.10), plus Phase 7's optional compatible-key bonus (0.06, never a penalty), energy-similarity bonus (0.04), and vocal-overlap-on-both-sides penalty (0.10). Every candidate carries a plain-text `reasons` list.

## Planner

`compile_automix()` (`app/automix/planner.py`): walks tracks in playlist order, preserves explicit `start_time_seconds` gaps verbatim, and for eligible adjacent pairs applies the best `TransitionCandidate`'s overlap by pulling the incoming clip's `timeline_start` earlier and cueing its `source_in` from the candidate's anchor. Only a BEAT_MATCH transition's *incoming* clip ever gets a non-1.0 `playback_rate` (the outgoing clip is never revisited once placed). Known v1 simplification: each pair's target BPM uses the outgoing track's raw analyzed BPM, not any rate an earlier transition already applied to it -- documented, bounded by `max_tempo_change_percent`, not yet observed to matter in testing.

## Audio Renderer

`AutoMixAudioPipeline` (`app/automix/renderer.py`): builds one `filter_complex` graph per export, folding clips left-to-right with `acrossfade` (transitions), silence-padded `concat` (explicit gaps), or plain `concat` (bare adjacency). Duration-validated via `ffprobe` against the plan within `DURATION_TOLERANCE_SECONDS` (0.15s); a mismatch deletes the output and raises rather than muxing a suspect result.

## Preview / UI

Project-level `automix_enabled` toggle (`ProjectSettingsDialog`), background analysis (`AutoMixAnalysisController`, QThread), BPM badge on playlist rows, full BPM/key/energy/quality summary in the track details dialog. Preview itself is unaffected (see "Features Deferred").

## Fallback Chain

Implemented at three levels:
1. **Per-transition** (`app/automix/candidates.py`): BEAT_MATCH -> BEAT_ALIGNED_CROSSFADE -> FIXED_CROSSFADE -> CUT, based on each track's `beat_alignment_quality()` and tempo compatibility.
2. **Per-pair in the planner** (`app/automix/planner.py`): missing analysis on one track, or the best candidate being CUT, degrades only that pair to plain sequential adjacency -- verified not to disturb neighboring pairs.
3. **Whole-export** (`app/renderer/ffmpeg_renderer.py`): any AutoMix dependency-import failure or render error falls back to the normal legacy sequential audio path entirely; only an actual cancellation propagates instead of falling back.
4. **Whole-application** (`app/controllers/automix_analysis_controller.py`, `app/renderer/ffmpeg_renderer.py`): a missing `librosa` installation degrades to "AutoMix unavailable," never to "the application will not start."

## Persistence

`ProjectSettings.automix_enabled: bool = False` (per-project, backward compatible -- a project file saved before this field existed loads with it off). No analysis data, beat arrays, or compiled plans are ever persisted to the project file; `AnalysisCache` is the only durable AutoMix storage, and it lives outside the project entirely.

## Packaging

`requirements.txt`/`requirements-lock.txt` add `librosa` and its transitive dependencies (`scipy`, `numba`/`llvmlite`, `scikit-learn`, `soundfile`, `pooch`, and smaller packages). `playlist_canvas.spec` adds `collect_all()` for the new packages, mirroring the existing `numpy` pattern. **Not build-tested against an actual PyInstaller build in this work** -- verify a packaged build launches and that AutoMix analysis/export both work from it before shipping a release. The lock-file versions were captured from a Python 3.14 dev sandbox, not the project's official Python 3.12 release environment; re-resolve on the real build machine.

AutoMix-OFF startup is verified not to require `librosa`: `app.ui.main_window` imports successfully with `librosa` blocked (verified via a `sys.meta_path` finder in this session), and both `app/controllers/automix_analysis_controller.py` and `app/renderer/ffmpeg_renderer.py` import the `librosa`-dependent modules lazily, inside their worker functions, wrapped in `try/except ImportError`.

## Performance

Not formally measured (no representative multi-track playlists available in this environment). Qualitatively: analysis parallelizes via a bounded `ThreadPoolExecutor` and dedupes identical source files; repeated exports/analyses of unchanged tracks are cache hits (cheap). The AutoMix audio render is one `filter_complex` pass per export (no per-track intermediate files beyond what FFmpeg's own filter graph needs).

## Licensing

No new dependency licenses beyond what `docs/automix-phase1-dependency-evaluation.md` already documents in full (librosa MIT-family; scipy/numba/scikit-learn/soundfile/pooch all permissive). Phase 7's key/energy/vocal-activity code adds no dependency: the Krumhansl-Schmuckler tone profiles are a published research constant, and the energy/vocal heuristics are original code. See that document's "Phase 2 outcome" and "Phase 7 outcome" sections for the complete per-package breakdown (license, redistribution, PyInstaller impact, model size).

## Test Results

Full repository suite as of this report: **743 passed, 18 skipped, 0 failed** (`python -m pytest tests/ -q`). Skips are the existing real-FFmpeg-gated tests (`PLAYLIST_CANVAS_TEST_FFMPEG` unset) plus a few pre-existing unrelated skips. All real-FFmpeg-gated AutoMix tests were additionally run once with a managed FFmpeg executable configured and passed (analysis, candidate generation, planner, renderer, and export-integration real-decode/render checks).

New test files: `test_automix_models.py`, `test_automix_cache.py`, `test_automix_analysis_service.py`, `test_automix_basic_analyzer.py`, `test_automix_key.py`, `test_automix_compatibility.py`, `test_automix_candidates.py`, `test_automix_planner.py`, `test_automix_renderer.py`, `test_automix_workflow.py`, `test_automix_analysis_controller.py`, `test_automix_ffmpeg_integration.py`. No unit test requires a real FFmpeg executable or `librosa` model download to run (real-FFmpeg tests are opt-in via an environment variable, matching the existing repository convention).

`scripts/run_tests.py`'s custom runner has a known pre-existing 180s per-module timeout that `test_main_window` occasionally exceeds (it passes directly under `pytest` in ~2-5 minutes depending on machine load); this is unrelated to AutoMix and was already true before this work began.

## Known Limitations

- AutoMix affects export audio only; video/Canvas timing and Preview are unaffected (see "Features Deferred").
- No loudness normalization in the AutoMix audio path.
- Tempo-drift compounding across a long chain of transitions is not modeled (each pair uses the outgoing track's raw analyzed BPM); bounded by `max_tempo_change_percent` but not eliminated.
- The vocal-activity heuristic is a coarse frequency-band proxy, not a real vocal detector, and will false-positive/negative on many real tracks.
- The analysis cache has no size cap or eviction policy.
- Packaging (PyInstaller) has not been build-tested in this work.
- No settings UI for transition bar length, tempo budget, or style -- only on/off.

## Release Blockers

Per roadmap section 18's checklist:

- **Preview and Export use different plans** -- true today, by deliberate scope decision (Preview never used AutoMix's plan; Export now does, for audio only). **This is the primary release-readiness gate**: shipping AutoMix as a user-facing feature while Preview does not reflect it, and video timing does not follow it, risks user confusion (an enabled toggle that visibly does nothing in Preview, and an export where visuals do not follow the blended audio). Recommend keeping the feature marked "beta" (already done in the UI copy) and treating the video/Preview integration as a required follow-up before removing that label.
- All other section 18 blockers (exported duration drift, cache corruption crashing startup/export, cancellation leaving FFmpeg running, AutoMix OFF changing legacy output, optional-dependency absence crashing the app, unclear licensing, multi-track cumulative drift) were specifically tested and found **not** present, per the sections above.

## Codex Review Notes

High-risk files for a follow-up review: `app/renderer/ffmpeg_renderer.py` (`render()`, `_render_automix_audio_segments`) for the audio/video duration-mismatch behavior when AutoMix shortens audio; `app/automix/planner.py` for the multi-track cumulative-placement and single-fixed-rate-per-clip logic; `app/automix/renderer.py`'s `build_filter_graph()` for FFmpeg filter labeling at scale (untested with a large number of tracks); `app/controllers/automix_analysis_controller.py` for Qt object lifetime (a real `libshiboken` crash was found and fixed here during this work -- worth a second look for any other lifetime edge case). Areas of explicit uncertainty: performance at 30+ tracks (untested), PyInstaller packaging (untested), and whether the "favor outgoing, no cascading" tempo policy will need revisiting once real multi-transition audio is evaluated by ear.
