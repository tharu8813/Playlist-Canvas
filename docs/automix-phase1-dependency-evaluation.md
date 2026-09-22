# AutoMix Phase 2 Analysis Backend — Dependency Evaluation

This note evaluates candidate BPM/beat/downbeat analysis backends for
`app/automix/analysis/`'s first real `AnalysisProvider` (Phase 2). No
dependency listed here is added in Phase 1 -- Phase 1 ships the
provider/service/cache boundary only, with no real analyzer behind it.

Evaluation criteria (roadmap 1.4/1.5): license, Python 3.12+/Windows status,
PyInstaller impact, native/binary dependencies, model size, BPM/beat/downbeat
ability, expected quality, and suitability as the always-on default vs. an
optional download.

## aubio

- **License**: GPLv3 (the Python bindings and the C library). GPL code
  cannot be linked into Playlist Canvas's proprietary-friendly distribution
  without triggering GPL obligations on the whole binary -- disqualifying
  for a bundled default per roadmap 1.5 ("do not copy GPL/AGPL implementation
  code ... unless the project licensing decision explicitly allows it").
- **Python/Windows**: prebuilt wheels are inconsistent across recent CPython
  versions on Windows; historically requires a C toolchain to build from
  source.
- **PyInstaller**: small native `.pyd`/`.dll`, low impact if it built at all.
- **Model size**: no ML model -- classic DSP (onset detection + tempo
  estimation). Tiny.
- **BPM**: yes. **Beats**: yes (onset-based). **Downbeats**: no built-in bar
  tracking.
- **Quality**: adequate for BPM, beat timestamps are onset-driven and less
  robust on heavily produced/EDM material than learned models.
- **Verdict**: excluded by license.

## librosa

- **License**: ISC (permissive). Its runtime dependencies (`numpy`,
  `scipy`, `soundfile`/`audioread`, `numba`, `joblib`, `scikit-learn`) are
  BSD/MIT-family -- redistribution-friendly.
- **Python/Windows**: well-maintained, pure-Python + compiled-dependency
  wheels available for current CPython on Windows.
- **PyInstaller**: `numba`'s LLVM JIT (`llvmlite`) is the main risk --
  large (tens of MB) and PyInstaller hooks for numba/llvmlite need explicit
  testing; `scipy`/`numpy` are already a comfortable known quantity (numpy
  is already bundled per `playlist_canvas.spec`). Realistic added
  installed size: tens of MB, not hundreds.
- **Model size**: no ML model -- `librosa.beat.beat_track` is classic
  onset-strength + dynamic-programming tempo tracking. Zero download.
- **BPM**: yes (`beat_track` returns a tempo estimate). **Beats**: yes
  (frame-aligned beat times). **Downbeats**: no native bar/meter tracker;
  would need a secondary heuristic (e.g. energy-weighted grouping of beats
  into bars of the estimated meter) or a dedicated downbeat model layered
  on top.
- **Quality**: solid general-purpose baseline; not competitive with
  learned downbeat trackers on syncopated or genre-atypical material, but
  predictable and explainable -- fits the roadmap's "boring transition that
  is correct" philosophy for a first release.
- **Verdict**: strongest default candidate. Permissive license, no model
  download, acceptable dependency weight, sufficient for BPM + beats. A
  simple heuristic (or a later Phase 7 add-on) covers downbeats.

## Beat This!

- **License**: MIT (code); the released pretrained checkpoint's license
  terms need separate confirmation from the model card before bundling,
  but the reference implementation itself is MIT.
- **Python/Windows**: requires **PyTorch** as a hard runtime dependency.
- **PyInstaller**: PyTorch alone is commonly 500 MB+ once CUDA/CPU wheel
  assets are included; this conflicts directly with roadmap 1.4 ("Do not
  add PyTorch ... to the default installation unless the phase explicitly
  allows it").
- **Model size**: tens of MB checkpoint, downloaded separately from the
  package.
- **BPM**: derivable from beats. **Beats**: state-of-the-art accuracy.
  **Downbeats**: yes -- this is its headline capability (joint beat +
  downbeat tracking), notably strong versus classic DSP methods.
- **Quality**: best-in-class beat/downbeat accuracy among the candidates
  evaluated.
- **Verdict**: excluded from the default install; strong candidate as an
  **optional downloadable engine** in Phase 7 ("optional advanced
  downloadable analysis engines") for users who accept the PyTorch cost
  for higher transition quality.

## Essentia

- **License**: dual-licensed AGPLv3 (open-source build) / commercial. The
  AGPL build cannot be linked into Playlist Canvas's distribution under
  the same reasoning as aubio, and the commercial license is a business
  decision outside engineering scope.
- **Python/Windows**: official wheels are Linux/macOS-focused; Windows
  support has historically required building from source with a fairly
  involved toolchain (Essentia's own C++ dependency graph).
- **PyInstaller**: large native library with many optional third-party
  codecs/algorithms statically linked in; heavy even before considering
  bundled models.
- **Model size**: its high-level rhythm/key/mood models are separate
  TensorFlow-based downloads, similar order of magnitude to Beat This!'s
  PyTorch cost.
- **BPM/Beats/Downbeats**: all supported, several algorithm choices per
  task.
- **Verdict**: excluded -- license (AGPL) and Windows packaging cost both
  independently disqualify it as a default; the commercial license removes
  the AGPL problem but is a licensing decision, not an engineering one, and
  is not assumed here.

## Lightweight custom / FFmpeg-assisted path

- **License**: N/A -- would reuse FFmpeg (already an external, user-managed
  binary in this project, not a bundled Python dependency) plus a small
  amount of original Python DSP code (autocorrelation or comb-filter tempo
  estimation over an `ebur128`/`silencedetect`- or `astats`-derived energy
  envelope).
- **Python/Windows**: zero new Python dependencies.
- **PyInstaller**: zero added size beyond what already ships.
- **Model size**: none.
- **BPM**: achievable with a basic autocorrelation/comb-filter approach on
  an onset-strength-like envelope, but building and validating this from
  scratch duplicates what `librosa.beat.beat_track` already provides.
  **Beats**: possible but materially more implementation and validation
  effort than librosa's tested function for a worse result.
  **Downbeats**: same limitation as librosa -- would still need a
  heuristic layer.
- **Quality**: the lowest of the group unless a substantial amount of new
  DSP code is written and tuned in-house.
- **Verdict**: kept as the last-resort fallback tier described in roadmap
  1.6 ("Fixed crossfade" / "Legacy sequential cut/concat"), not as the
  primary Phase 2 analyzer -- reimplementing what librosa already provides
  is not a good use of engineering effort for a first release.

## Recommendation for Phase 2

- **Default analyzer**: `librosa` (`beat.beat_track` for BPM + beat
  timestamps), with a simple beat-grouping heuristic for an approximate
  downbeat/meter estimate. Permissive license, no model download, and the
  dependency weight (numpy already bundled; scipy/numba/llvmlite added) is
  the smallest of the "real analyzer" options.
- **Optional advanced engine (Phase 7 candidate)**: **Beat This!**, gated
  behind an explicit opt-in and separate model download, given its PyTorch
  cost and superior downbeat accuracy.
- **Excluded**: aubio and Essentia's open-source build (GPL/AGPL,
  incompatible with roadmap 1.5 by default); Essentia's commercial tier
  and any other paid option are a licensing/business decision, not made
  here.
- **Always-available fallback**: the lightweight FFmpeg-assisted path,
  reserved for the bottom of the fallback chain (roadmap 1.6) when no
  analyzer is available or analysis fails, not as a quality-competitive
  primary analyzer.

## Phase 2 outcome

`librosa` was adopted as `app/automix/analysis/basic.py`'s `BasicAnalysisProvider`
(`provider_id="basic"`), confirming the recommendation above. Audio is
decoded to mono float32 PCM via the project's own managed FFmpeg (piped,
no temp file) rather than through librosa's own `audioread`/`soundfile`
loading path, so container/codec support tracks whatever FFmpeg already
handles elsewhere in the app.

## Phase 7 outcome

Key, energy, and vocal-activity estimation (`app/automix/analysis/key.py`,
and the additions to `app/automix/analysis/basic.py`) add **no new
dependency** -- all three reuse `librosa`/`numpy`, already covered above.

- **Key**: Krumhansl-Schmuckler major/minor tone-profile correlation
  (Krumhansl & Kessler, 1982) is a published, decades-old music-cognition
  research result, not a licensed software model -- the twelve profile
  numbers used here are a standard, widely-reproduced constant, not
  redistributed third-party code or weights.
- **Energy**: a plain RMS-over-a-reference-level heuristic, original code.
- **Vocal activity**: a plain voice-band (300-3400 Hz) energy-ratio
  heuristic over short windows, original code -- not a trained model, and
  explicitly documented (module docstring, roadmap Phase 7 section 5's
  "lightweight" tier) as a coarse proxy rather than a real vocal detector.

No optional advanced beat/downbeat engine (e.g. Beat This!) was added in
Phase 7: the "Beat This!" evaluation above (PyTorch cost, ~500MB+) still
applies, and the roadmap explicitly allows implementing only the subset
of Phase 7 that is justified (section 16 / "It is acceptable to implement
only a subset"). It remains a candidate for a future phase if a
downloadable-model UX is built.

Installed transitive footprint, captured from a Python 3.14 dev sandbox
(`pip install librosa`): `scipy` 1.18.1, `numba` 0.67.0 + `llvmlite` 0.49.0
(the largest single wheel, ~43 MB), `scikit-learn` 1.9.1, `soundfile`
0.14.0, plus `pooch`, `soxr`, `joblib`, `msgpack`, `cloudpickle`,
`lazy-loader`, `narwhals`, `threadpoolctl`, `platformdirs`. This is a real
increase from the project's previous three-dependency baseline
(`PySide6`, `mutagen`, `numpy`) and has **not** been verified against an
actual PyInstaller build in this change -- `playlist_canvas.spec` was
updated to `collect_all()` the new packages the same way it already does
for `numpy`, but a packaged build should be run and AutoMix analysis
exercised from it before this ships in a release.

## AutoMix v3 outcome (Beat This! integration)

The "optional downloadable engine" recommendation above was implemented as
`app/automix/analysis/beat_this.py`'s `BeatThisAnalysisProvider`, selected
via the new `app/automix/analysis/registry.py::create_analysis_provider("beat_this", ...)`
instead of always constructing `BasicAnalysisProvider` directly. It is a
**hybrid** provider: it delegates decode, silence handling, key, energy,
and vocal-activity entirely to an owned `BasicAnalysisProvider` instance,
and only replaces the rhythm fields (`bpm`, `bpm_confidence`, `beats`,
`downbeats`, `meter_*`) with the model's own beat/downbeat output.

- **Upstream**: `CPJKU/beat_this` on GitHub, published to PyPI as
  `beat-this` (verified at integration time: `pip install beat-this`,
  version 1.1.0). **License**: MIT for both the code and the released
  pretrained checkpoint (confirmed directly from the upstream `LICENSE`
  file and README, superseding this doc's earlier "needs separate
  confirmation" note on the model weights). **Dependencies**: `torch>=2`,
  `torchaudio`, `numpy>=1.20`, `einops`, `rotary-embedding-torch`, `soxr`
  -- none pinned with an upper bound upstream. **Not added to
  `requirements.txt`**: this remains a genuinely optional dependency, per
  the original evaluation above and roadmap 1.4/1.5 -- torch alone is
  commonly 500 MB+, so the app, its default install, and its test suite
  must all keep working with it completely absent.
- **API used**: `beat_this.inference.File2Beats(checkpoint_path="final0",
  device=..., dbn=False)`, called as `beats, downbeats =
  file2beats(path_str)`. The default checkpoint (`"final0"`, ~78 MB) is
  downloaded and cached by the `beat_this` package's own inference code on
  first use, in its own cache directory -- this integration does not
  implement a separate download/checksum/staging pipeline for the model
  weights (unlike `app/ffmpeg/managed_installer.py`'s FFmpeg download),
  since `beat_this` already owns that; a corrupted/interrupted download or
  any other inference-time failure surfaces as an ordinary exception from
  the `file2beats(...)` call, which `BeatThisAnalysisProvider.analyze()`
  catches and degrades to the basic analyzer's own result for that one
  track.
- **Device selection**: CUDA if `torch.cuda.is_available()`, else CPU --
  never required, never forced.
- **Lazy import**: `torch`/`beat_this` are imported only inside
  `BeatThisAnalysisProvider._load_model()`, called on first use of an
  instance, never at module or package import time -- constructing the
  provider itself (`create_analysis_provider("beat_this", ...)`) does not
  import either.
- **Confidence calibration**: documented in
  `app/automix/analysis/beat_this.py`'s `_bpm_from_beats`/`_meter_confidence`
  docstrings -- both derive a 0.0-1.0 confidence from the median absolute
  deviation of (bar-)interval consistency around the median interval,
  `confidence = clamp(1 - 3 * MAD/median, 0, 1)`, with `_meter_confidence`
  additionally discounted by downbeat coverage relative to the beat count.
  No constant is hardcoded to force `TrackAnalysis.beat_alignment_quality()`
  into `"reliable"` -- a track only reaches it by actually having a steady
  model-detected beat/downbeat grid, exactly like
  `RELIABLE_BPM_CONFIDENCE`/`RELIABLE_METER_CONFIDENCE` already require.
- **Not implemented in this phase**: structure analysis (intro/outro/
  section/energy-curve), planner v2 phrase/structure-aware candidate
  generation, and effective-BPM propagation across chained transitions
  remain future work (see the roadmap's P2/P3 tiers) -- this phase is
  scoped to P0 (the analysis engine) plus wiring a provider-selection point
  at both existing call sites
  (`AutoMixAnalysisController.start(..., provider_id=...)` and
  `FFmpegRenderer._render_automix_audio_segments`, the latter still
  hardcoded to `"basic"` pending an actual settings UI toggle).

## AutoMix v3 P1 outcome (activation + real-model validation)

P0 above shipped `BeatThisAnalysisProvider` and a selection point, but
nothing in the real app actually selected it -- `MainWindow`'s AutoMix
analysis call and Export/Preview rendering both still resolved to
`"basic"`. P1 wires real usage and fixes three correctness issues found
while doing so.

- **Provider selection is now actually wired**: `app/automix/analysis/registry.py`
  gained an `"auto"` policy (`create_analysis_provider("auto", ...)` ->
  Beat This! if `beat_this_available()` -- `importlib.util.find_spec` for
  `torch`/`beat_this`, which resolves importability without importing
  either -- else basic). `MainWindow._maybe_start_automix_analysis()`
  (the playlist-badge analysis path) and
  `FFmpegRenderer._render_automix_audio_segments` (Preview/Export
  rendering) both now pass `"auto"`, so they can never resolve to a
  different analyzer for the same playlist.
- **Fallback cache poisoning (confirmed and fixed)**: `AnalysisCache` is
  namespaced by `provider.provider_id`/`version`, fixed at
  `AnalysisService` construction -- but a hybrid provider's *fallback*
  result carries the fallback's own `analyzer_id` (e.g. `"basic"`), not
  the provider's. `AnalysisService.store()`'s envelope used the fixed
  namespace regardless, so a `"beat_this"`-unavailable fallback got cached
  under the `"beat_this"` namespace, and `load()` did not check whether
  the cached result's own `analyzer_id` actually matched -- a real
  regression matching the reported scenario exactly. Fixed in
  `AnalysisService._analyze_one`: a cache hit is only accepted when
  `cached.analyzer_id == self.provider.provider_id`; a provenance mismatch
  is treated as a miss and re-analyzed. Regression test:
  `tests/test_automix_analysis_service.py::test_fallback_result_is_not_cached_as_a_provider_success`
  (unavailable -> fallback cached; engine "recovers" -> next run must
  actually re-invoke it; genuine success then cached and reused).
- **`basic_result.bpm is None` skip condition was wrong** (confirmed):
  it conflated "librosa found no *usable* BPM" with "the track is
  silent/too short," which are not the same thing, and could make Beat
  This! never even run on a track Basic's beat tracker simply failed on --
  exactly the case Beat This! exists to help with. Fixed to check
  `basic_result.energy is None` instead: `energy` is left at its
  dataclass default `None` only by `BasicAnalysisProvider`'s own early
  return for a silent/too-short track; every other path through
  `analyze()` (including "beats found but BPM rejected as implausible")
  always computes a real energy value. Regression test:
  `tests/test_automix_beat_this.py::test_attempts_inference_when_basic_found_no_bpm_on_a_non_silent_track`.
- **Cache identity now includes checkpoint + installed package version**:
  `BeatThisAnalysisProvider.__init__` sets `self.version` to
  `"{implementation_version}+{checkpoint}+pkg{beat-this package version}"`
  (via `importlib.metadata.version("beat-this")`, metadata-only, no
  torch import), so switching checkpoints or upgrading the `beat-this`
  package both invalidate previously cached results automatically.
- **Unknown `provider_id` now raises** `ValueError` instead of silently
  degrading to `"basic"` -- a typo/wiring bug should be loud, not hidden;
  `"auto"` remains the deliberate "degrade if unavailable" choice. Both
  call sites catch `ValueError` alongside `ImportError` and log at error
  level without crashing.

### Real Beat This! 1.1.0 / `final0` run (this session, CPU, no CUDA available)

`pip install beat-this` succeeded (`beat-this-1.1.0`, `torch-2.14.0+cpu`,
`torchaudio-2.11.0`, plus `einops`/`rotary-embedding-torch`/`sympy`/
`fsspec`/`filelock`/`jinja2`/`mpmath`/`MarkupSafe`). First use downloaded
the `final0` checkpoint (77.3 MB, from
`https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt`,
cached at `~/.cache/torch/hub/checkpoints/beat_this-final0.ckpt`) in ~8s.
`tests/test_automix_beat_this_real_model.py` (opt-in,
`PLAYLIST_CANVAS_TEST_BEAT_THIS=1`) passed both tests end to end --
inference validity, and a full `compile_automix()` +
`AutoMixAudioPipeline.render()` pass.

A manual, throwaway script (two synthetic 3-minute click+tone tracks, 128
and 132 BPM) additionally confirmed, with real numbers:

```
cache identity: provider_id='beat_this' version='1+final0+pkg1.1.0'

Track A: analyzer_id=beat_this, bpm=130.43, bpm_confidence=1.000,
         beats=385, downbeats=385, meter_confidence=1.000,
         beat_alignment_quality() = 'reliable'
Track B: analyzer_id=beat_this, bpm=130.43, bpm_confidence=1.000,
         beats=397, downbeats=397, meter_confidence=1.000,
         beat_alignment_quality() = 'reliable'

Basic-only analyze() for the same 180s track: bpm=129.20, beats=383,
downbeats=96, meter_confidence=0.3, beat_alignment_quality() = 'bpm_only'

compile_automix(): 1 transition, type=BEAT_MATCH, start=172.50s,
duration=7.50s -- a real BEAT_MATCH transition was actually selected,
which Basic's own "bpm_only" tier could never produce (candidates.py
requires "reliable" on both sides).
```

CPU timing (same 180s track): Basic-only 7.15s; Beat This! first call
(includes model construction/warmup) 19.49s; second call with the model
already loaded and reused 11.45s; additional Beat This! cost over
basic-only analysis once the model is warm: **~4.3s per 3-minute track on
CPU**. Every track analyzed after the first in a batch pays only this
warm cost, not the ~8-12s load/warmup, since `AnalysisService` shares one
provider instance across the whole batch and `BeatThisAnalysisProvider`
loads its model at most once (`_load_model`, lock-guarded).

Both synthetic tracks converged to the same reported BPM (130.43) despite
being generated at different target BPMs (128/132) -- plausible for this
specific synthetic click+sine-tone stress signal (not real music), not
investigated further; flagged here rather than silently reported as a
clean result. **Root-caused in P1.5 below**: the fixture had no bar accent
at all, so the model had no signal to distinguish downbeats from other
beats and reported every beat as also a downbeat (`beats == downbeats`,
`meter_confidence == 1.0`) -- the code accepted that uncritically.

## AutoMix v3 P1.5 outcome (meter sanity validation)

The P1 real-model run above (385/385 and 397/397 beats/downbeats,
`meter_confidence == 1.000` for both) was re-examined: a 4/4 track should
have roughly `beats/4` downbeats, not `beats/1`. Root cause was the
fixture, not the model or the swap direction of `file2beats`'s return
values (confirmed against the real API) -- every click in
`_write_click_wav` was acoustically identical, so nothing in the signal
distinguished a bar's first beat from the others, and the model (or its
postprocessor) defaulted to marking every detected beat as a downbeat too.
The real bug is that `BeatThisAnalysisProvider` accepted this uncritically:
the old `_meter_confidence` only checked whether downbeat-to-downbeat
*time* intervals were regular, which a degenerate 1:1 "every beat is a
downbeat" prediction trivially passes (a perfectly regular beat grid is
also a perfectly regular "1-beat bar" grid).

**New meter validation** (`app/automix/analysis/beat_this.py`):

- `_beats_per_bar_counts`: for each pair of consecutive downbeats, counts
  how many detected beats fall in between via `np.searchsorted` -- the
  same integer-counting idea Beat This! 1.1.0's own new
  `beat_this.utils.infer_beat_numbers()` (added per its changelog; verified
  directly against the installed 1.1.0 source, `beat_this/utils.py`) uses
  to number beats within a bar. That upstream function itself was not
  used directly: it requires all downbeats to already be a subset of
  beats (raises `ValueError` otherwise) and uses `print()` for its warning
  paths, neither of which fits a confidence-calibration call site that
  must degrade to zero confidence instead of raising/printing on malformed
  input -- but the core per-bar counting technique is the same.
- `_estimate_meter_numerator`: takes the *mode* (most common) beats-per-bar
  count across all bars and how consistent that count is
  (`MINIMUM_BAR_COUNT_CONSISTENCY = 0.6`, i.e. at least 60% of bars must
  agree). Returns `None` when the mode falls outside a plausible bar
  length (`MINIMUM/MAXIMUM_PLAUSIBLE_BEATS_PER_BAR = 2..7`) or bars
  disagree too much -- this is what rejects the degenerate "every beat is
  a downbeat" case outright (every bar has exactly 1 beat, `1 < 2`).
- `_downbeat_alignment_score`: a defensive check that each downbeat
  actually sits within `_DOWNBEAT_ALIGNMENT_TOLERANCE_SECONDS = 0.08` of
  some detected beat (Beat This!'s own postprocessing already snaps
  downbeats onto beats, so this is expected to be a no-op on real model
  output, not a load-bearing check).
- `_meter_confidence` now combines all of the above (bar-length
  plausibility gate first, then time-interval regularity, alignment, and
  coverage) multiplicatively -- an implausible bar-length estimate forces
  confidence straight to `0.0` regardless of how "regular" the raw
  downbeat timing looks.
- `meter_numerator`/`meter_denominator` are no longer hardcoded to `4, 4`
  whenever any downbeats exist; they come from `_estimate_meter_numerator`,
  and are `None, None` when no plausible bar length was found.
  `meter_denominator` is always reported as `4` when a numerator is found
  (simple-meter assumption; Beat This! doesn't distinguish e.g. 6/8 from
  3/4 from beat spacing alone -- a known, documented limitation, not
  addressed here).
- Per roadmap "never postprocess model output to look plausible":
  `beats`/`downbeats` are still reported exactly as the model predicted
  them even when the implied meter is rejected -- there is no
  `downbeats[::4]` truncation or similar "fix" anywhere in this code. An
  implausible meter is communicated entirely through
  `meter_numerator=None` and `meter_confidence`, which
  `beat_alignment_quality()`/`candidates.py` already use to gate
  `BEAT_MATCH` -- no planner change was needed for this to take effect.

**Regression tests** (`tests/test_automix_beat_this.py`): the literal
"every beat reported as a downbeat" case never reaching
`RELIABLE_METER_CONFIDENCE`, the two `malformed`/`overprediction` cases
from the roadmap (100 beats/100 downbeats -> low confidence; 100 beats/25
correctly-spaced downbeats -> high confidence), a pure-function 3/4
fixture correctly estimating numerator 3 (not silently mislabeled 4/4), an
inconsistent-bar-count fixture correctly refusing to guess, and an
end-to-end `BeatThisAnalysisProvider.analyze()` test confirming the
model's downbeats array is reported unmodified even when the meter is
rejected.

**New accented real-model fixture**
(`tests/test_automix_beat_this_real_model.py::_write_accented_click_wav`):
beat 1 of every bar is a louder, low-frequency (130 Hz) thump; beats 2-4
are quieter, higher-pitched (1400 Hz) clicks -- replacing the old fixture
where every beat was acoustically identical.

**Re-run against the real model (accented fixture, this session)**:

```
Track A, 128 BPM target, 60s:
  bpm=~128-130 (within tolerance), beats=384, downbeats=97
  beats_per_bar (actual) = 3.96, meter_numerator=4, meter_confidence=0.990
  beat_alignment_quality() = 'reliable'
```

`tests/test_automix_beat_this_real_model.py`'s
`test_accented_four_four_track_is_detected_as_a_reliable_meter` and
`test_compiles_into_a_valid_automix_plan_and_selects_beat_match` (two 128
BPM accented tracks) both passed: a real `BEAT_MATCH` transition was
selected, with `meter_confidence` correctly high and `beat_alignment_quality()
== "reliable"` for both tracks.

**An honestly-reported new finding, not smoothed over**: re-running the
same accented fixture at 132 BPM for longer synthetic tracks (180s)
produced far fewer detected beats than the click pattern actually contains
(256 detected vs. ~396 expected for one run; a shorter 45s take at the
same BPM showed the same pattern, 56 vs. ~99). The *reported BPM* still
landed close to correct (derived from whichever beats were detected, which
remained evenly spaced), but beat *coverage* was degraded enough that
`beats_per_bar` came out non-integer-ish (e.g. 2.56), and
`_estimate_meter_numerator` correctly returned `None` for the 180s case
instead of confidently reporting a wrong meter -- i.e. the P1.5 fix did
its job on real model output, not just synthetic unit-test fixtures. This
looks like a genuine model/fixture interaction specific to this synthetic
click pattern at this tempo (a real music track would not have perfectly
periodic clicks), not investigated further, and not something this session
attempted to fixture-tune away -- flagged here as an open question rather
than hidden. `test_two_tracks_at_different_bpm_are_both_measured_accurately`
(45s duration, BPM-tolerance assertion only, matching the roadmap's
specific ask) still passes, since BPM itself stayed within tolerance
despite the reduced beat coverage.

**CPU timing (accented fixture, 180s track)**: Basic-only 4.48s; Beat
This! first call (model load+warmup) 8.97s; second call (warm model)
5.70s; additional Beat This! cost over basic-only once warm: **~1.2s per
3-minute track** on this run (down from the ~4.3s measured on the
unaccented fixture in P1 -- both numbers are from the same class of
synthetic audio and a small sample size; treat as an order-of-magnitude
estimate, not a precise benchmark).

**Real music**: not available in this sandboxed environment (no licensed
audio files present, and none were downloaded or committed per roadmap
"do not commit copyrighted audio") -- honestly reported as not done, not
assumed to work. Recommended follow-up for whoever has local music files:
run `BeatThisAnalysisProvider(ffmpeg).analyze(...)` on a few real tracks
(pop/EDM, band/acoustic, syncopated) and compare
`beat_alignment_quality()`/`meter_numerator` against ear/known tempo.

**CUDA**: not available in this environment (`torch.cuda.is_available()`
is `False`, CPU-only wheel installed) -- device selection
(`"cuda" if torch.cuda.is_available() else "cpu"`) was verified by code
reading and by the CPU branch actually running above, not by an actual
CUDA run. This remains unverified on real GPU hardware; flagged as a real
limitation, not assumed to work.

### Storage

`beat-this` + `torch` (CPU wheel) + transitive dependencies: the `torch`
wheel alone was 124.1 MB downloaded; combined with `torchaudio`,
`einops`, `rotary-embedding-torch`, `sympy`, `fsspec`, `filelock`,
`jinja2`, `mpmath`, `MarkupSafe`, and `beat-this` itself, total download
was on the order of 150-200 MB, plus the installed (unpacked) size which
is typically larger for torch specifically (commonly 500 MB-1 GB
installed for a CPU wheel; not measured precisely in this session). The
`final0` checkpoint adds 77.3 MB on first use. None of this affects the
app's default install or its normal (non-opt-in) test run -- confirmed:
`scripts/run_tests.py` and every non-opt-in AutoMix test suite complete in
well under a second of AutoMix-related work even with `beat-this`
installed on this machine, since normal tests never construct a working
FFmpeg + `transition_mode="automix"` + enough real playback/analysis to
reach the `"auto"` resolution with a functioning decode path.

