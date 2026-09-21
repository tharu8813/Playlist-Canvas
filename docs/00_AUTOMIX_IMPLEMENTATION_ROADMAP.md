# Playlist Canvas AutoMix Implementation Roadmap

Repository: `https://github.com/tharu8813/Playlist-Canvas`

## 0. Purpose

This document is the master execution guide for implementing AutoMix after the RenderPlan architecture preparation has been completed.

The current architecture is considered **frozen** unless a concrete blocker is discovered.

Current execution flow:

```text
Project
  ↓
Playlist
  ↓
Timeline
  ↓
Compiler / Planner
  ↓
CompiledRenderPlan
  ├─ AudioRenderPlan
  ├─ PresentationPlan
  └─ MetadataPlan
       ↓
Preview / Export
```

The implementation must now proceed by adding AutoMix capabilities *inside this architecture*, not by redesigning the architecture again.

---

## 1. Global rules

### 1.1 Do not redesign the core architecture

Do not introduce a second timeline system, a second preview planner, or a second export timing model.

`CompiledRenderPlan` remains the common execution contract.

### 1.2 Preserve legacy mode

When AutoMix is disabled:

- existing playlist timing must remain unchanged;
- existing export audio must remain unchanged;
- Preview and Export must behave as before;
- existing `.pvsproj` files must remain loadable;
- no AutoMix analysis should be required.

### 1.3 One phase at a time

Each phase has its own MD file.

Only execute the phase explicitly requested by the user.

Do **not** automatically continue to the next phase.

At the end of every phase:

1. list changed files;
2. list created files;
3. explain the implementation;
4. report tests;
5. report regressions or limitations;
6. state whether the next phase is safe to begin;
7. make one focused commit if the working tree policy allows it.

### 1.4 Avoid dependency explosion

Do not add PyTorch, Demucs, TensorFlow, Essentia, or other heavy stacks to the default installation unless the phase explicitly allows it.

Prefer a small default installation and optional model downloads.

### 1.5 Licensing is part of engineering

Before adding a dependency:

- verify its current license;
- verify redistribution conditions;
- verify PyInstaller implications;
- document the result;
- prefer MIT/BSD/Apache/ISC-style components when practical.

Do not copy GPL/AGPL implementation code into Playlist Canvas unless the project licensing decision explicitly allows it.

### 1.6 Never silently degrade export

Analysis failure should degrade **one transition**, not destroy the whole export.

Fallback chain should eventually reach:

```text
AutoMix
  ↓ failure
Beat-aware transition
  ↓ failure
Fixed crossfade
  ↓ failure
Legacy sequential cut/concat
```

The exact fallback tiers are introduced progressively by the phase documents.

---

## 2. Phase order

### Phase 1
`01_PHASE1_ANALYSIS_FOUNDATION_CACHE.md`

Build the AutoMix package, analysis data models, cache, provider/service boundary, cancellation/progress model, and dependency/licensing evaluation scaffolding.

No real beat model yet.

### Phase 2
`02_PHASE2_BPM_BEAT_DOWNBEAT.md`

Implement the first real track analyzer:

- BPM
- beat timestamps
- downbeat/bar information when available
- confidence
- robust fallback

### Phase 3
`03_PHASE3_TRANSITION_COMPATIBILITY_CANDIDATES.md`

Build transition intelligence without rendering:

- candidate generation
- BPM compatibility
- bar/phrase-length reasoning
- transition score
- safe fallback classification

### Phase 4
`04_PHASE4_AUTOMIX_PLANNER.md`

Create `AutoMixPlanner` that converts analysis + playlist order into a `CompiledRenderPlan`.

Still no production DSP renderer.

### Phase 5
`05_PHASE5_FFMPEG_AUTOMIX_RENDERER.md`

Implement actual AutoMix audio generation:

- overlap
- beat alignment
- controlled tempo matching
- equal-power crossfade
- fallback rendering
- N-track output

### Phase 6
`06_PHASE6_PREVIEW_UI_WORKFLOW.md`

Expose AutoMix to the user:

- settings
- analysis state
- transition information
- transition preview
- Preview/Export plan sharing

### Phase 7
`07_PHASE7_ADVANCED_ANALYSIS_KEY_ENERGY_VOCAL.md`

Optional quality upgrades:

- key compatibility
- energy
- vocal activity
- optional advanced downloadable analysis engines
- advanced transition styles

### Phase 8
`08_PHASE8_FINAL_INTEGRATION_RELEASE_AUDIT.md`

Production hardening:

- end-to-end regression
- packaging
- performance
- cache migration
- cancellation
- error handling
- licensing
- release audit
- Codex-ready final review checklist

---

## 3. Suggested package target

Do not create all files immediately if they are not needed yet, but the long-term package may evolve toward:

```text
app/automix/
├─ __init__.py
├─ models.py
├─ settings.py
├─ cache.py
├─ analysis/
│  ├─ __init__.py
│  ├─ provider.py
│  ├─ service.py
│  ├─ basic.py
│  └─ advanced.py
├─ compatibility.py
├─ candidates.py
├─ planner.py
├─ preview.py
└─ renderer.py
```

The exact split may be adapted to the current repository style.

Do not create speculative files with no implementation.

---

## 4. Core data-flow target

```text
Audio Files
    ↓
AnalysisService
    ↓
AnalysisCache
    ↓
TrackAnalysis[]
    ↓
TransitionCandidateGenerator
    ↓
Compatibility / Scoring
    ↓
AutoMixPlanner
    ↓
CompiledRenderPlan
    ├ AudioRenderPlan
    ├ PresentationPlan
    └ MetadataPlan
         ↓
FFmpeg AutoMix Renderer
         ↓
Mixed Audio
         ↓
Existing Canvas / Export Pipeline
```

Preview and Export must consume the same compiled plan.

---

## 5. Quality philosophy

The first successful AutoMix version does not need to imitate every Apple Music transition.

The order of importance is:

1. no timing corruption;
2. no export regressions;
3. rhythmically sensible transitions;
4. predictable fallback;
5. acceptable audio quality;
6. richer DJ behavior later.

A boring transition that is correct is better than an impressive transition that drifts off beat or corrupts the timeline.

---

## 6. Architecture invariants

These invariants should survive every phase.

### Timing invariant

```text
Same CompiledRenderPlan
→ same global time meaning
→ same Presentation owner
→ same chapter/timestamp boundary
```

### Persistence invariant

Analysis results are cache data, not primary project document data.

### Preview invariant

Preview must not independently re-plan transitions.

### Export invariant

Final audio duration must match `CompiledRenderPlan.duration_seconds` within an explicitly documented encoding tolerance.

### Fallback invariant

A single bad track analysis must not automatically abort the complete project.

---

## 7. Development-agent workflow

Before each phase:

1. inspect the current repository;
2. read this roadmap;
3. read only the active phase document plus files it references;
4. inspect current tests before changing code;
5. print a concise implementation plan;
6. implement;
7. run targeted tests;
8. run the required wider regression set;
9. update relevant docs;
10. stop.

Do not start the next phase.

---

## 8. Codex final-review strategy

The development agent is expected to perform the implementation.

Codex will later perform a final audit focusing on:

- architecture drift;
- duplicate timing logic;
- regression risks;
- FFmpeg filter correctness;
- cancellation and cleanup;
- cache invalidation;
- packaging;
- licensing;
- test gaps;
- edge cases across many tracks.

Therefore every phase should leave code that is easy to inspect rather than clever but opaque.

---

## 9. Start condition

Begin Phase 1 only if:

- `CompiledRenderPlan` architecture exists;
- Presentation source-time mapping is implemented;
- Presentation gap semantics are fixed;
- Timeline transitions are preserved in AudioRenderPlan;
- primary Preview/Export timing is RenderPlan-based;
- Metadata timing is unified;
- regression tests are green apart from known test-runner timeout behavior.

If any of these assumptions are false, report the mismatch before changing code.
