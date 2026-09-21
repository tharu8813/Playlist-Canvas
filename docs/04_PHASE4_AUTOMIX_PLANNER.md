# Phase 4 — AutoMix Planner → CompiledRenderPlan

## 0. Goal

Implement `AutoMixPlanner`.

The planner consumes:

- playlist/timeline;
- TrackAnalysis map;
- Phase 3 candidate logic;
- AutoMix settings;

and produces the existing `CompiledRenderPlan`.

This is the phase where AutoMix becomes a real timeline plan.

Do not implement production DSP rendering yet.

---

## 1. Core invariant

Preview and Export must not understand how AutoMix was decided.

They should only receive a `CompiledRenderPlan`.

Target:

```text
Timeline
 + analyses
 + settings
      ↓
 AutoMixPlanner
      ↓
CompiledRenderPlan
```

The output shape must remain compatible with the existing architecture.

---

## 2. Planner API

Choose an API consistent with existing compiler conventions.

Conceptually:

```python
def compile_automix(
    timeline: Timeline,
    tracks: Sequence[PlaylistTrack],
    analyses: Mapping[str, TrackAnalysis],
    settings: AutoMixSettings,
) -> CompiledRenderPlan:
    ...
```

Or introduce planner selection through the existing compile options only if it remains clean.

Do not turn `compile_timeline()` into a giant switch statement.

---

## 3. Planning each transition

For every adjacent enabled pair:

```text
A → B
```

1. load analyses;
2. run Phase 3 compatibility;
3. generate candidates;
4. choose the best safe candidate;
5. compute source cues;
6. compute overlap duration;
7. compute playback rates;
8. produce AudioRenderClip placement;
9. produce AudioRenderTransition;
10. choose Presentation switch;
11. update Metadata boundaries.

---

## 4. Audio clip layout

Example:

```text
A source 0 --------------------- 200
timeline 0 --------------------- 200

B source        8 ------------------------- 208
timeline            188 ------------------- 388
```

Overlap:

```text
188 → 200
```

Total duration becomes shorter than sequential duration.

This must be reflected in:

```text
CompiledRenderPlan.duration_seconds
```

---

## 5. Source cue mapping

Use `source_in`, `source_out`, and `playback_rate` correctly.

Do not lie to PresentationPlan.

If B begins from source 8.0s at timeline 188.0s:

```text
Presentation source_time_at_start
```

must eventually represent the correct B local time at the visual switch.

---

## 6. Presentation ownership

Audio overlap and visual ownership are separate.

For each A→B transition choose a deterministic Presentation switch.

Initial recommended rule:

- switch to B at the chosen incoming musical anchor/downbeat;
- if unavailable, use a defined point inside the overlap, such as the midpoint or B fade-in anchor.

Do not switch randomly.

Presentation windows must remain non-overlapping.

---

## 7. Metadata

Metadata chapters/timestamps must follow Presentation ownership.

Chapter start for B should be the B Presentation start, not necessarily B's first low-volume audio sample.

Use the established MetadataPlan policy.

---

## 8. Playback rate

Respect the Phase 3 safe tempo range.

The planner may produce rates such as:

```text
A = 1.012
B = 0.992
```

but must never silently exceed policy.

If the required rate is unsafe, downgrade strategy.

---

## 9. Transition representation

Populate:

```python
AudioRenderPlan.transitions
```

with real planned transitions.

Transition type should describe the required render behavior.

Possible first-version types:

```text
CUT
CROSSFADE
EQUAL_POWER
BEAT_MATCH
```

Reserve `AUTOMIX` only if it has a clear renderer meaning.

---

## 10. Fallback planning

Per pair, fallback safely.

Example:

```text
Beat match
 ↓
Beat-aligned equal-power
 ↓
Fixed equal-power
 ↓
Cut
```

One bad pair must not invalidate later pairs.

---

## 11. Multi-track accumulation

Pay special attention to 3+ tracks.

A→B overlap changes B's global placement, which changes B→C global placement.

Do not compute every transition against the original sequential global starts and then stitch them.

Build the mixed timeline cumulatively and test it.

---

## 12. Edge cases

Handle:

- one track;
- two tracks;
- disabled tracks;
- explicit legacy gaps;
- very short tracks;
- missing analysis;
- unsafe tempo difference;
- clips already trimmed;
- playback rate already not 1.0 if supported by Timeline V2;
- zero-duration transition fallback.

Document how explicit user gaps interact with AutoMix.

For v1, preserving explicit requested gaps rather than mixing across them is a reasonable conservative policy.

---

## 13. No DSP yet

Phase 4 output is plan data.

Do not invoke FFmpeg to produce the final mixed playlist.

A developer diagnostic render may be acceptable only if isolated and clearly not the production renderer, but prefer not to add it here.

---

## 14. Plan validation

Add a pure validator or assertions/tests covering:

- no invalid negative timeline starts;
- source bounds valid;
- Presentation windows ordered/non-overlapping;
- chapter starts ordered;
- plan duration equals actual compiled end;
- transition references existing clips;
- playback rates positive and safe;
- no accidental NaN/inf.

---

## 15. Tests

Must include:

### 2-track compatible BPM

Verify overlap and shorter total duration.

### 3-track accumulation

Verify A→B and B→C result in correct global positions.

### Missing analysis on middle pair

Only that transition falls back.

### Unsafe tempo

No beat-match rate emitted.

### Presentation switch

Verify global→local mapping during and after transition.

### Metadata

Chapter starts equal Presentation starts.

### Disabled track

Skipped cleanly.

### Determinism

Same input → exactly equal plan.

### Legacy off

Sequential compiler remains unchanged.

---

## 16. Optional diagnostic output

A compact debug representation is valuable:

```text
A 0.000 → 200.000 rate=1.000
transition A→B 188.000 → 200.000 BEAT_MATCH
B 188.000 → 388.000 rate=0.992
presentation B @ 194.000 source=14.000
```

Keep it logging/debug-only.

---

## 17. Do not do

Do not:

- add FFmpeg mixing filters;
- modify project schema for analysis;
- add AutoMix UI;
- implement key/energy/vocal scoring;
- redesign RenderPlan.

---

## 18. Completion criteria

Phase 4 is complete when:

- AutoMixPlanner produces valid `CompiledRenderPlan`;
- total duration reflects overlaps;
- Presentation and Metadata align;
- fallback pairs work independently;
- sequential mode remains untouched;
- tests cover multi-track planning.

---

## 19. End-of-phase report

Report:

```text
## Phase 4 Summary
## Planner API
## Timeline Construction
## Presentation Switch Policy
## Metadata Policy
## Tempo Policy
## Fallbacks
## Plan Validation
## Tests
## Known Limitations
## Phase 5 Readiness
```

Then stop.
