# Phase 8 — Final Integration, Regression & Release Audit

## 0. Goal

Turn the implemented AutoMix stack into a release-quality Playlist Canvas feature.

This phase should primarily **verify, repair, simplify, and document**.

Do not use this phase as an excuse for a new architecture rewrite.

---

## 1. End-to-end target

```text
Import tracks
   ↓
AutoMix enabled
   ↓
Analysis cache
   ↓
Track analysis
   ↓
Transition candidates
   ↓
AutoMixPlanner
   ↓
CompiledRenderPlan
   ↓
Preview
   ↓
Export
   ↓
Final MP4
```

Preview and Export must agree on:

- duration;
- transition boundaries;
- Presentation owner;
- chapter/timestamp starts.

---

## 2. Audit architecture drift

Search for duplicate timing/planning logic.

Review calls involving:

```text
resolve_track_windows(
playlist_duration(
compile_playlist(
compile_timeline(
AutoMixPlanner
PresentationPlan
MetadataPlan
```

Allowed legacy usage should remain isolated to legacy editor/adapter semantics.

No new secret timeline math should have appeared in UI or renderer helpers.

---

## 3. Regression matrix

Test at least:

### AutoMix OFF

- one track;
- many tracks;
- explicit gaps;
- disabled tracks;
- lyrics;
- Now Playing;
- album art;
- progress;
- visualizer;
- video sources;
- chapters;
- YouTube timestamps;
- project save/load.

### AutoMix ON

- one track;
- two compatible tracks;
- two incompatible tracks;
- 10+ tracks;
- mixed analysis quality;
- explicit gap;
- short track;
- long track;
- unusual file names;
- missing file;
- cancelled analysis;
- cancelled render.

---

## 4. Duration audit

For AutoMix exports compare:

```text
CompiledRenderPlan.duration_seconds
PreparedAudio.duration_seconds
PreparedVideo.duration_seconds
Final MP4 duration
```

Define explicit tolerance.

Any larger drift is a bug.

---

## 5. A/V synchronization

Test transition boundaries where:

- Presentation owner switches;
- lyrics reset;
- progress resets;
- album art changes;
- track-linked video changes;
- chapter changes.

Check several minutes into a multi-transition export to detect cumulative drift.

---

## 6. Audio quality audit

Listen/measure:

- no clicks at cuts;
- no unexpected silence holes;
- no clipping;
- no large volume dip at equal-power midpoint;
- no tempo runaway;
- no missing intro/outro due to cue math;
- fallback transitions remain sane.

Use synthetic signals for measurable tests and real user-owned/test media for manual listening where available.

---

## 7. Cache audit

Test:

- cache survives restart;
- file modification invalidates;
- analyzer update invalidates;
- corrupted cache repairs;
- stale path does not poison a new file;
- cache directory can be deleted safely;
- concurrent analysis does not corrupt entries.

Document approximate cache size.

---

## 8. Failure/fallback audit

Simulate:

- analyzer exception;
- no beat data;
- FFmpeg filter error;
- one corrupt audio file;
- cache write failure;
- optional model missing;
- optional model download failure.

The user should receive a controlled message or transition fallback.

No traceback-only UX.

---

## 9. Cancellation/cleanup audit

Cancel during:

- analysis;
- transition preview render;
- full AutoMix audio render;
- final video encode.

Verify:

- processes stop;
- threads stop;
- UI unlocks;
- temp files clean up;
- incomplete output is not reported as success.

---

## 10. Performance audit

Measure representative playlists:

```text
2 tracks
10 tracks
30 tracks
```

Track:

- first analysis time;
- cached analysis time;
- planning time;
- audio mix render time;
- peak memory;
- temporary disk use.

Look for obvious O(N²) work in normal adjacent-track planning.

---

## 11. Packaging

Build the actual distributable configuration.

Verify:

- PyInstaller;
- hidden imports;
- native DLLs;
- optional dependency absence;
- FFmpeg discovery/install;
- model directory;
- cache directory;
- clean machine startup if practical.

AutoMix OFF must not crash because an optional analyzer/model is absent.

---

## 12. Licensing audit

Create/update notices for every new dependency/model.

For each:

```text
component
version
license
source
redistribution notes
binary/model notes
```

Pay particular attention to:

- FFmpeg build license configuration;
- optional ML model weights;
- Rubber Band if ever introduced;
- Essentia if ever introduced;
- copied code from external DJ projects.

Do not ship uncertain licensing.

---

## 13. Documentation

Update:

- README;
- AutoMix user guide;
- architecture guide;
- troubleshooting;
- optional model installation;
- cache location/reset;
- known limitations.

Avoid claiming the feature is an Apple Music clone.

Use neutral wording such as:

```text
beat-aware automatic playlist transitions
```

---

## 14. Test suite

Run the complete repository suite.

Record:

- total modules;
- pass;
- fail;
- timeout;
- known pre-existing flakes.

If `test_main_window` still exceeds the custom runner timeout but passes directly, document exact command/results and consider whether runner timeout should be adjusted separately.

Do not mark a real failure as a timeout artifact without verifying it.

---

## 15. New dedicated integration tests

Recommended:

```text
test_automix_analysis_cache.py
test_automix_rhythm_analysis.py
test_automix_candidates.py
test_automix_planner.py
test_automix_ffmpeg_integration.py
test_automix_preview_consistency.py
```

Names may follow repository conventions.

Ensure unit tests do not all depend on heavy optional models.

---

## 16. Codex handoff package

Prepare a final audit summary that Codex can inspect.

Include:

```text
architecture diagram
new dependencies
new persistent/cache locations
planner rules
fallback chain
FFmpeg strategy
tests
known limitations
high-risk files
```

Also identify areas where the development agent is uncertain.

Do not hide questionable code behind “works on my machine.”

---

## 17. Codex review checklist

Ask the final reviewer to inspect:

1. duplicate timing logic;
2. RenderPlan correctness;
3. Presentation source-time mapping;
4. cumulative multi-track placement;
5. FFmpeg filter labeling/mapping;
6. tempo-rate math;
7. duration validation;
8. cancellation;
9. subprocess cleanup;
10. cache atomicity/invalidation;
11. thread safety;
12. Preview/Export drift;
13. chapters/timestamps;
14. project backward compatibility;
15. PyInstaller;
16. dependency licenses;
17. optional-model failure;
18. test blind spots.

---

## 18. Release blocker criteria

Do not call AutoMix release-ready if any of these remain:

- Preview and Export use different plans;
- exported audio duration materially differs from plan;
- cache corruption crashes startup/export;
- cancellation leaves FFmpeg running;
- AutoMix OFF changes legacy output unexpectedly;
- optional dependency absence crashes the app;
- unclear dependency/model license;
- multi-track transitions accumulate timing drift.

---

## 19. Completion report

Final report:

```text
# AutoMix Final Implementation Report

## Architecture
## Features Implemented
## Features Deferred
## Default Analysis Engine
## Optional Engines / Models
## Cache
## Candidate / Scoring Rules
## Planner
## Audio Renderer
## Preview / UI
## Fallback Chain
## Persistence
## Packaging
## Performance
## Licensing
## Test Results
## Known Limitations
## Release Blockers
## Codex Review Notes
```

Stop after the final audit. Do not begin unrelated feature work.
