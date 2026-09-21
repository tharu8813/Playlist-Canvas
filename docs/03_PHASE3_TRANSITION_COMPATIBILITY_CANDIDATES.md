# Phase 3 — Transition Compatibility & Candidate Generation

## 0. Goal

Build the decision layer that answers:

> Given Track A and Track B analysis, where *could* a safe transition occur, and how suitable is each candidate?

This phase must not render audio.

Outputs should be deterministic planning data that Phase 4 can consume.

---

## 1. Preconditions

Phase 2 must provide usable:

- BPM;
- beats;
- downbeats/bar starts;
- confidence.

The Phase 3 code must tolerate incomplete analysis.

---

## 2. New concepts

Suggested immutable models:

```python
TransitionCandidate
TransitionCompatibility
TransitionDecisionReason
```

Possible candidate fields:

```python
from_track_id
to_track_id

outgoing_source_time
incoming_source_time

bars
duration_seconds

outgoing_bpm
incoming_bpm
target_bpm

outgoing_rate
incoming_rate

score
confidence

strategy
reasons
```

Do not bake FFmpeg commands into these models.

---

## 3. Candidate generation

Generate candidates around musically sensible boundaries.

Start conservative.

Preferred candidate lengths:

```text
4 bars
8 bars
16 bars
```

Default preference can be 8 bars.

Candidate points should generally align to:

- beat;
- preferably downbeat;
- suitable outro region of A;
- suitable intro region of B.

Phase 3 may use heuristic intro/outro windows if section detection is not yet implemented.

---

## 4. Source-region policy

Do not destroy large portions of songs in the first AutoMix version.

Conservative defaults:

- outgoing transition near A's end;
- incoming transition near B's beginning;
- avoid skipping large intro sections without an explicit reason.

Keep source cue values separate from timeline placement.

---

## 5. BPM compatibility

Build a pure compatibility helper.

Consider:

- direct BPM difference;
- percentage tempo shift required;
- optional half/double equivalence.

Example:

```text
A = 128
B = 132
```

could be compatible.

```text
A = 90
B = 170
```

may be compatible only through double-time interpretation, not a raw 89% stretch.

Set explicit safe tempo-change limits.

Do not use unbounded `atempo`.

---

## 6. Target BPM policy

Implement a deterministic target-BPM rule.

Possible conservative policies:

- favor outgoing track;
- favor incoming track;
- midpoint;
- weighted midpoint based on confidence.

Document the selected rule.

The selected policy must not exceed the allowed tempo-change percentage.

---

## 7. Candidate score

Use explainable scoring.

Example weighted concepts:

```text
beat/downbeat confidence
tempo compatibility
transition length availability
cue proximity to expected intro/outro
tempo shift amount
analysis confidence
```

Do not introduce ML in this phase.

The score should not be magic-only. Store reasons.

Example:

```text
+ downbeats high confidence
+ 8 bars available
+ tempo delta only 2.1%
- incoming cue begins after 18s
```

---

## 8. Fallback classification

Phase 3 should classify each pair into a strategy family, for example:

```text
BEAT_MATCH
BEAT_ALIGNED_CROSSFADE
FIXED_CROSSFADE
CUT
```

This is a planning classification only.

No audio processing yet.

---

## 9. Weak analysis

Examples:

### BPM known, beats poor

Allow a simpler timed crossfade.

### BPM difference too high

Do not force beat match.

### No analysis

Use fixed crossfade candidate if enough source duration exists.

### Very short track

Shorten or disable overlap.

---

## 10. Determinism

Given identical settings and analysis, candidate order and selected best candidate must be stable.

Do not use unseeded randomness.

---

## 11. Settings boundary

Introduce planner-facing AutoMix settings if needed, but do not expose full UI yet.

Possible fields:

```python
enabled
preferred_bars
max_tempo_change_percent
min_transition_seconds
max_transition_seconds
allow_half_double_tempo
fallback_crossfade_seconds
```

Keep defaults conservative.

---

## 12. Compatibility API

Prefer pure functions/classes.

Conceptually:

```python
compatibility = evaluate_compatibility(a, b, settings)

candidates = generate_candidates(a, b, compatibility, settings)
```

No Qt.
No FFmpeg process.
No project mutation.

---

## 13. Tests

Build dense unit coverage.

Cases:

- equal BPM;
- close BPM;
- exact double tempo;
- unsafe tempo gap;
- high-confidence downbeats;
- no downbeats;
- no beats;
- no BPM;
- short outgoing track;
- short incoming track;
- 4/8/16 bar candidates;
- stable deterministic score;
- tempo ratio limit;
- fallback classification.

Use handcrafted `TrackAnalysis` objects for most tests.

Do not make unit tests depend on the real analyzer.

---

## 14. Documentation

Document scoring enough that a later developer can tune weights without reverse engineering.

A table is useful:

```text
Factor                  Effect
Downbeat confidence     +
Tempo shift             -
8-bar availability      +
Cue too deep in intro   -
```

---

## 15. Do not do

Do not:

- create CompiledRenderPlan yet;
- render FFmpeg transitions;
- modify Preview UI;
- implement key/energy/vocal scoring;
- add heavy AI.

---

## 16. Completion criteria

Phase 3 is complete when:

- A/B pairs produce candidates;
- safe beat-match candidates exist when appropriate;
- unsafe pairs fall back;
- candidate decisions are explainable;
- tests cover degraded-analysis cases;
- no renderer dependency exists.

---

## 17. End-of-phase report

Report:

```text
## Phase 3 Summary
## Candidate Model
## BPM Compatibility Rules
## Target BPM Rule
## Candidate Generation Rules
## Scoring
## Fallback Classification
## Tests
## Known Limitations
## Phase 4 Readiness
```

Then stop.
