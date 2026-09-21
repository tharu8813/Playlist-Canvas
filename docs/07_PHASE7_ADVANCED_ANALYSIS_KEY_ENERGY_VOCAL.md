# Phase 7 — Advanced AutoMix Quality: Key, Energy & Vocal Awareness

## 0. Goal

Improve transition quality after the basic system is proven stable.

This phase is optional-quality work, not foundational rescue work.

Add only features that measurably improve selection/planning.

Potential areas:

- musical key;
- Camelot compatibility;
- energy;
- vocal activity;
- optional advanced beat/downbeat engine;
- advanced transition styles.

---

## 1. Rule: keep the basic engine usable

The default AutoMix path must continue to work without downloading large models.

Advanced features should degrade cleanly when unavailable.

Target concept:

```text
Base AutoMix
→ always available

Advanced analysis
→ optional
→ downloadable
→ cached
```

---

## 2. Key detection

Add:

```text
key
mode
confidence
```

Normalize output to a stable representation.

If using Camelot compatibility, convert internally in a dedicated helper.

Do not store arbitrary backend-specific strings as permanent public semantics.

---

## 3. Harmonic compatibility

Initially use key as a **score modifier**, not an absolute blocker.

Do not pitch-shift songs automatically in this phase unless explicitly scoped later.

Possible behavior:

```text
compatible key → longer mix gets bonus
poor key match → shorter/smoother transition
```

---

## 4. Energy

Create a normalized energy feature with clearly defined computation.

Possible sources:

- loudness envelope;
- spectral flux;
- band energy;
- RMS/LUFS-related normalized features.

Do not confuse loudness with musical energy without documenting the heuristic.

Use energy primarily for transition style/scoring.

---

## 5. Vocal activity

Goal:

Avoid obvious vocal-on-vocal clashes.

Possible implementation tiers:

### Lightweight

Use spectral/onset/voice heuristics.

### Advanced optional

Use stem separation or a model that estimates vocal activity.

Do not require full Demucs stem export if only vocal-activity windows are needed.

---

## 6. Optional advanced beat/downbeat engine

If Phase 2's default analyzer is lightweight, Phase 7 may integrate an optional stronger model such as Beat This! if current license, runtime, model distribution, and packaging are acceptable.

Requirements:

- download separately;
- versioned model manifest;
- checksum;
- model location in user data;
- uninstall/repair state;
- fallback to default analyzer.

Reuse FFmpeg/model-install patterns if the project already has them.

---

## 7. Model manager

Only add a model manager if an actual model is integrated.

Needed capabilities:

```text
not installed
downloading
installed
version
verify checksum
remove
failure/retry
```

Do not build a generic plugin marketplace.

---

## 8. Candidate scoring integration

Extend Phase 3 scoring with optional features.

Example:

```text
rhythm compatibility
+ harmonic compatibility
+ energy continuity
- vocal overlap penalty
```

Keep reasons inspectable.

Example diagnostic:

```text
Score 0.82
+ downbeat confidence
+ compatible tempo
+ compatible key
- vocal overlap 1.8s
```

---

## 9. Advanced transition styles

Only after data supports them.

Possible:

```text
smooth
beat_mix
bass_swap
filter_sweep
```

Implement one at a time.

Do not add ten named styles that all map to the same filter graph.

---

## 10. EQ / bass swap

If implemented:

- define band split;
- avoid clipping;
- validate perceived loudness;
- use automation curves tied to transition timing.

Keep v1 simple and testable.

---

## 11. No automatic reorder

Do not reorder playlist tracks automatically in this phase.

The user-selected order remains authoritative.

A future separate feature may suggest ordering.

---

## 12. Cache versioning

Advanced features may have independent provider versions.

Avoid invalidating expensive beat analysis merely because key analysis algorithm changed if cache architecture can separate sub-results cleanly.

Only split cache domains if the complexity is justified.

---

## 13. Tests

Key:

- normalization;
- Camelot mapping;
- compatibility.

Energy:

- deterministic normalized output;
- silence/low-energy;
- louder/high-energy synthetic cases.

Vocal:

- empty/no-vocal fallback;
- activity intervals valid;
- scoring penalty.

Optional model manager:

- checksum;
- version;
- missing model fallback;
- interrupted download cleanup where testable.

Planner:

- same basic result when advanced data unavailable;
- advanced data changes score, not architecture correctness.

---

## 14. Performance

Advanced analysis may be expensive.

Measure:

- analysis time per track;
- model load time;
- memory;
- cache effectiveness.

Load heavy models once per analysis session where appropriate.

Do not reload a multi-hundred-MB model for every track.

---

## 15. Licensing

Document every added model/library:

```text
name
version
license
redistribution
model license
commercial/non-commercial constraints
source URL
```

The code license and model-weights license may differ.

Check both.

---

## 16. Completion criteria

Phase 7 is complete when chosen advanced features:

- improve planning without being mandatory;
- degrade safely;
- are cached;
- are licensed/documented;
- do not break packaging;
- retain basic AutoMix path.

It is acceptable to implement only a subset if the others are not justified.

---

## 17. End-of-phase report

Report:

```text
## Phase 7 Summary
## Advanced Features Added
## Key Analysis
## Energy Analysis
## Vocal Awareness
## Optional Models
## Model Management
## Planner Changes
## Audio Style Changes
## Performance
## Licensing
## Tests
## Features Deferred
## Phase 8 Readiness
```

Then stop.
