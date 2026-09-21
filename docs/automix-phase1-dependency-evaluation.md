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
