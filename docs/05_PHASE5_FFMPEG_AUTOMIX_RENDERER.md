# Phase 5 — FFmpeg AutoMix Audio Renderer

## 0. Goal

Render the Phase 4 `AudioRenderPlan` into real mixed audio.

Initial production scope:

- clip trimming;
- overlap;
- controlled tempo matching;
- beat-aligned scheduling from the plan;
- equal-power crossfade;
- N-track playlist;
- fallback behavior;
- exact duration integration with existing export.

Do not add advanced EQ/key/vocal tricks yet.

---

## 1. Architecture boundary

This phase is the correct point to introduce an audio pipeline boundary.

Target:

```text
AudioRenderPlan
      ↓
AudioPipeline
  ├ LegacySequentialAudioPipeline
  └ AutoMixAudioPipeline
      ↓
PreparedAudio
      ↓
existing final video mux
```

Do not rewrite the visual renderer.

---

## 2. PreparedAudio

Introduce a small result object if useful:

```python
@dataclass(frozen=True, slots=True)
class PreparedAudio:
    path: Path
    duration_seconds: float
```

May include codec/sample format metadata only if genuinely used.

---

## 3. Legacy path

When AutoMix is disabled, preserve existing normalize/silence/concat behavior.

Avoid routing legacy audio through a new complex filter graph if unnecessary.

Regression equivalence matters more than architectural purity.

---

## 4. AutoMix clip preparation

For each `AudioRenderClip` apply:

- `source_in`;
- `source_out`;
- `playback_rate`;
- gain if already semantically supported;
- standard sample rate;
- stereo layout.

Use FFmpeg.

Do not load full PCM into Python merely for mixing.

---

## 5. Tempo matching

Use an FFmpeg-supported strategy within the allowed safe range.

If using `atempo`:

- validate supported values;
- chain filters only when needed;
- remember `atempo` changes duration;
- ensure plan duration assumptions and rendered duration agree.

If a higher-quality stretcher is considered, check licensing first.

Do not add Rubber Band casually without handling its licensing implications.

---

## 6. Transition DSP v1

Implement:

### CUT

No overlap effect beyond plan placement.

### CROSSFADE

Simple robust crossfade.

### EQUAL_POWER

Preferred default smooth transition.

### BEAT_MATCH

For v1 this can mean:

```text
planned beat-aligned overlap
+ planned tempo adjustment
+ equal-power crossfade
```

It does not need complex DJ EQ yet.

---

## 7. Filter graph strategy

Avoid building an unreadable monolithic string inline in `FFmpegRenderer`.

Use helper builders.

Conceptually:

```text
input 0 → trim/rate/resample → delay → gain ┐
input 1 → trim/rate/resample → delay → fade ├→ mix
input 2 → ...                               ┘
```

Or render transition segments if that is substantially more reliable.

Choose a design that supports many tracks without exceeding practical command complexity.

Document the chosen strategy.

---

## 8. Equal-power curve

Use a documented FFmpeg curve or mathematically equivalent strategy.

The center of a transition should not have an obvious volume hole.

Do not assume linear crossfade is equal-power.

Add an integration test or signal-level check if practical.

---

## 9. Duration correctness

This is critical.

The produced mixed audio duration should match:

```text
CompiledRenderPlan.duration_seconds
```

within a small documented tolerance.

Do not simply mux with `-shortest` and hide planning mistakes.

Validate the prepared audio.

---

## 10. Gaps

Explicit planned gaps must remain silent.

Do not accidentally bridge them with an overlap.

The AutoMix planner should already define behavior, but renderer must honor it exactly.

---

## 11. Failure containment

If rendering a sophisticated transition fails, define how to degrade.

Do not silently generate corrupt audio.

Possible retry:

```text
BEAT_MATCH filter failure
→ rerender pair/plan as EQUAL_POWER if safe
```

However, avoid mutating the plan invisibly if that would desynchronize visuals.

Prefer planning fallback before render.

Renderer fallback must preserve timeline duration and Presentation timing, or report a controlled error.

---

## 12. Cancellation

Every FFmpeg subprocess must respect existing cancellation behavior.

On cancel:

- terminate subprocesses;
- delete incomplete temporary files;
- do not leave a fake cache/result;
- preserve existing export cancellation semantics.

---

## 13. Progress

Useful stages:

```text
Preparing AutoMix audio
Preparing clips
Rendering transitions
Combining mix
Validating mixed audio
```

Progress should be meaningful for N tracks.

---

## 14. Visualizer interaction

Do not fully redesign visualizer policy in this phase unless necessary.

But final export visualizer should ideally react to the mixed master, not independent sequential originals.

If current visualizer rendering assumes per-track audio, document the exact limitation and make the smallest safe integration.

Do not allow a knowingly wrong timeline silently.

---

## 15. Tests

### Synthetic two-track render

Use generated tones/click tracks.

Verify:

- output exists;
- duration;
- no FFmpeg error;
- overlap reduces total duration.

### Three-track render

Verify cumulative timing.

### Tempo adjustment

Verify expected duration and no drift.

### Gap

Verify silence remains.

### Cancellation

Verify cleanup.

### Special paths

Unicode/space file paths.

### Legacy regression

AutoMix OFF still uses legacy audio result.

### Metadata/video mux

Final MP4 duration validation still succeeds.

---

## 16. Performance

Avoid one full uncompressed intermediate per entire playlist if a filter graph can safely avoid it.

But reliability is more important than maximum optimization.

Measure:

- temp disk;
- process count;
- rough render time;
- memory.

Do not guess.

---

## 17. Do not do

Do not implement:

- key shifting;
- harmonic mixing;
- bass swap;
- vocal-aware mixing;
- Demucs;
- production AutoMix UI.

---

## 18. Completion criteria

Phase 5 is complete when:

- AutoMix plan renders to real audio;
- 2+ tracks work;
- tempo-aware equal-power transitions work;
- duration matches plan;
- explicit gaps survive;
- cancellation works;
- legacy export remains stable.

---

## 19. End-of-phase report

Report:

```text
## Phase 5 Summary
## Audio Pipeline Structure
## FFmpeg Graph Strategy
## Tempo Implementation
## Crossfade Implementation
## Duration Validation
## Cancellation
## Performance
## Tests
## Legacy Regression
## Known Audio Limitations
## Phase 6 Readiness
```

Then stop.
