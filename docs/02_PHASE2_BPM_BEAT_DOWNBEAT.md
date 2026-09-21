# Phase 2 — BPM, Beat & Downbeat Analysis

## 0. Goal

Implement the first real AutoMix analyzer on top of Phase 1 infrastructure.

Outputs must include, when confidence permits:

- BPM;
- beat timestamps;
- downbeat timestamps or equivalent bar starts;
- meter assumption/estimate;
- confidence information.

This phase still does **not** create transitions or render mixed audio.

---

## 1. Preconditions

Do not begin unless Phase 1 provides:

- `TrackAnalysis`;
- cache;
- provider abstraction;
- analysis service;
- cancellation;
- progress;
- dependency evaluation.

If the Phase 1 design differs, adapt to the implemented contracts instead of duplicating them.

---

## 2. Select the default analyzer deliberately

Before coding, inspect the Phase 1 dependency evaluation.

Choose the smallest reliable default that satisfies:

- Windows;
- Python version used by Playlist Canvas;
- PyInstaller;
- acceptable license;
- BPM;
- beat timestamps;
- reasonable install size.

If high-quality downbeats require a heavy model, use a two-tier design:

```text
Default analyzer
→ BPM + beats + basic bar inference

Optional advanced analyzer
→ stronger beat/downbeat model
```

Do not force a multi-hundred-MB ML runtime into the default package merely to satisfy downbeat quality.

---

## 3. Required analysis pipeline

Conceptually:

```text
Audio file
  ↓
Decode / resample mono analysis signal
  ↓
Onset / rhythm features
  ↓
Tempo estimate
  ↓
Beat tracking
  ↓
Beat-grid cleanup
  ↓
Downbeat/bar inference or advanced downbeat output
  ↓
Confidence / validation
  ↓
TrackAnalysis
```

Do not load an entire huge uncompressed playlist into memory at once.

Analysis is per track.

---

## 4. Audio decoding

Prefer the project's FFmpeg availability where practical.

Normalize analysis input to a consistent format such as:

```text
mono
22050 / 44100 / 48000 Hz
float32 or suitable PCM
```

The exact rate should be chosen based on the selected analyzer.

Avoid decoding to giant temporary WAV files unless required.

Streaming/chunking is preferred when supported.

---

## 5. BPM

Return BPM only when valid.

Handle common half/double-tempo ambiguity.

Examples:

```text
75 ↔ 150
87 ↔ 174
```

Do not automatically force every track into a single BPM range without recording the decision.

Create helper logic that can later compare tempo classes for compatibility.

Recommended plausible range for conventional playlist music can be configurable rather than magic-scattered constants.

---

## 6. Beat timestamps

Beat timestamps must be:

- sorted;
- finite;
- non-negative;
- within the analyzed duration;
- deduplicated within a sensible tolerance.

Do not store sample indexes as public API.

Use seconds.

---

## 7. Downbeats / bar starts

If the selected default analyzer directly returns downbeats, store them.

If not, Phase 2 may infer a provisional 4/4 grid using beat sequence and an alignment heuristic.

If inference is used:

- mark confidence accordingly;
- document that it is provisional;
- do not pretend inferred downbeats have ML-level certainty.

This distinction is important for later fallback decisions.

---

## 8. Meter

Defaulting to 4/4 may be acceptable for low-confidence fallback, but the assumption must be explicit.

Possible model fields:

```text
meter_numerator = 4
meter_denominator = 4
meter_confidence
```

If `TrackAnalysis` does not yet have meter confidence, add only what is genuinely needed.

---

## 9. Confidence

Confidence must help the planner decide whether to trust beat-aware transitions.

Avoid one fake confidence number if the underlying analyzer supplies separate confidence concepts.

At minimum planner-friendly information should distinguish:

```text
high enough for beat alignment
usable BPM but uncertain downbeat
analysis failed / insufficient
```

This may be represented by numeric values plus helper methods.

---

## 10. Validation and repair

Real-world analysis can produce garbage.

Add post-processing that can reject or repair:

- duplicate beats;
- impossible BPM;
- huge beat discontinuities;
- timestamps outside duration;
- empty arrays;
- inconsistent beat spacing.

Do not silently invent a perfect beat grid over the entire song if evidence is weak.

---

## 11. Cache integration

A cached Phase 1 result must automatically invalidate when the analyzer implementation/version changes.

Bump provider/analyzer version whenever output semantics change.

Do not bump cache schema for every algorithm tweak if analyzer version is sufficient.

---

## 12. Progress

Suggested stages:

```text
Decoding audio
Analyzing rhythm
Tracking beats
Resolving bars
Validating result
```

Cancellation should be checked between stages.

---

## 13. Developer diagnostics

Provide a developer-friendly way to inspect analysis, for example a debug JSON dump or test helper.

Do not add a full production UI yet.

A useful diagnostic includes:

```json
{
  "bpm": 128.1,
  "bpm_confidence": 0.91,
  "beat_count": 412,
  "downbeat_count": 103,
  "first_beats": [0.48, 0.95, 1.42],
  "analyzer": "..."
}
```

---

## 14. Synthetic test fixtures

Create generated audio fixtures when practical.

Examples:

- 120 BPM click;
- 128 BPM click;
- 150 BPM click;
- short silence;
- weak/noisy onset case.

Fixtures should be generated in tests or kept tiny.

Do not add copyrighted songs.

---

## 15. Tests

At minimum:

### BPM accuracy

For synthetic clicks, define a documented tolerance.

### Beat alignment

Expected beat timestamps should be close enough for transition planning.

### Half/double tempo

Verify normalization/comparison helper behavior.

### Silence

Should return low confidence/failure, not nonsense high-confidence BPM.

### Very short file

Graceful result.

### Cache

Second analysis uses cache.

### Cancellation

Stops safely.

### Corrupt/unsupported audio

Returns controlled failure through the Phase 1 service contract.

---

## 16. Packaging

If a dependency is added:

- update requirements;
- update PyInstaller spec if needed;
- document native binaries;
- test import from a packaged-like environment where possible.

Do not defer known packaging problems to the final phase.

---

## 17. Do not do

Do not implement:

- transition candidate scoring;
- key matching;
- energy matching;
- vocal detection;
- audio crossfade;
- AutoMix Planner;
- UI controls.

---

## 18. Completion criteria

Phase 2 is complete when:

- real audio produces BPM;
- real/synthetic audio produces beat timestamps;
- downbeat/bar information exists with explicit confidence/assumption;
- bad inputs degrade safely;
- results cache correctly;
- package impact is documented;
- tests and regressions pass.

---

## 19. End-of-phase report

Report:

```text
## Phase 2 Summary
## Analyzer Chosen
## License / Packaging
## Decode Path
## BPM Strategy
## Beat Strategy
## Downbeat Strategy
## Confidence Semantics
## Cache Version
## Tests
## Accuracy / Tolerances
## Known Failure Cases
## Phase 3 Readiness
```

Then stop.
