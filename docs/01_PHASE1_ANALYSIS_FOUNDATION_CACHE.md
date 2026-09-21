# Phase 1 — AutoMix Analysis Foundation & Cache

## 0. Goal

Build the foundation required for AutoMix analysis without implementing a production beat detector yet.

At the end of this phase Playlist Canvas must have:

- AutoMix analysis data models;
- a versioned cache;
- a provider interface;
- an analysis service;
- progress/cancellation support;
- robust cache invalidation;
- zero impact on legacy export when AutoMix is unused.

Do **not** implement AutoMix planning or audio mixing in this phase.

---

## 1. Read first

Inspect at least:

```text
app/timeline/models.py
app/timeline/render_plan.py
app/timeline/compiler.py
app/models/project.py
app/models/playlist.py
app/services/app_settings_service.py
app/renderer/ffmpeg_renderer.py
app/utils/subprocess_utils.py
tests/test_render_plan.py
playlist_canvas.spec
```

Also inspect existing patterns for:

- application data directories;
- cache directories;
- QThread / ThreadPoolExecutor use;
- cancellation;
- logging;
- versioned resources;
- FFmpeg probing.

Reuse repository conventions.

---

## 2. Package boundary

Introduce `app/automix/` if it does not yet exist.

Suggested minimum:

```text
app/automix/
├─ __init__.py
├─ models.py
├─ settings.py
├─ cache.py
└─ analysis/
   ├─ __init__.py
   ├─ provider.py
   └─ service.py
```

Adapt names if the repository already has a better convention.

Do not create planner or renderer implementations yet.

---

## 3. TrackAnalysis model

Create an immutable analysis result model.

Suggested fields:

```python
@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    track_id: str
    source_path: str
    duration_seconds: float

    bpm: float | None = None
    bpm_confidence: float = 0.0

    beats: tuple[float, ...] = ()
    downbeats: tuple[float, ...] = ()

    meter_numerator: int | None = None
    meter_denominator: int | None = None

    key: str | None = None
    key_confidence: float = 0.0

    energy: float | None = None
    vocal_activity: tuple[tuple[float, float], ...] = ()

    analyzer_id: str = ""
    analyzer_version: str = ""
```

Do not force unused advanced fields to be populated yet.

Validate:

- finite non-negative timestamps;
- sorted timestamp arrays;
- duration boundaries;
- finite BPM;
- confidence in a defined range, preferably `0.0..1.0`.

Do not store a `PlaylistTrack` object inside the analysis model.

---

## 4. Analysis cache

Implement a versioned on-disk cache.

Recommended location follows the repository's normal per-user data directory, conceptually:

```text
%LOCALAPPDATA%/PlaylistCanvas/automix-cache/
```

Do not hard-code a Windows-only path if an existing cross-platform helper exists.

### Cache key

The cache must not trust path alone.

Use enough fingerprint data to detect replacement/changes, such as:

```text
canonical path
file size
mtime_ns
```

Optionally include a lightweight content fingerprint if practical without reading the entire file.

Do not hash multi-hundred-MB audio files on every startup unless a clear reason exists.

### Cache envelope

Example:

```json
{
  "schema_version": 1,
  "analyzer_id": "basic",
  "analyzer_version": "1",
  "file": {
    "path": "...",
    "size": 123456,
    "mtime_ns": 123456789
  },
  "analysis": {
    "...": "..."
  }
}
```

### Invalidate when

- schema changes;
- analyzer changes;
- analyzer version changes;
- file size changes;
- mtime changes;
- cache JSON is malformed;
- required fields are invalid.

A bad cache entry must be ignored and replaceable, not crash the application.

---

## 5. Provider interface

Create a small analysis provider protocol/ABC.

Conceptually:

```python
class AnalysisProvider(Protocol):
    provider_id: str
    version: str

    def analyze(
        self,
        track: PlaylistTrack,
        *,
        cancel_event: threading.Event,
        progress: Callable[[float, str], None] | None = None,
    ) -> TrackAnalysis:
        ...
```

Avoid over-engineering.

The point is to let Phase 2 supply one implementation and Phase 7 optionally supply more.

---

## 6. Analysis service

Create an orchestration layer that:

1. receives tracks;
2. checks cache;
3. analyzes only misses;
4. stores successful results;
5. reports per-track and total progress;
6. supports cancellation;
7. returns results keyed by stable track ID.

Suggested behavior:

```python
AnalysisService.analyze_tracks(...)
→ dict[str, TrackAnalysis]
```

Do not silently reuse a result for a different track ID if the media is not the same.

If two PlaylistTracks reference the exact same media, cache reuse is allowed.

---

## 7. Concurrency

Analysis must be callable off the UI thread.

The core service itself should not depend on Qt if avoidable.

The caller may later wrap it in QThread/QRunnable.

If parallelism is implemented:

- keep worker count bounded;
- make cache writes thread-safe;
- do not oversubscribe CPU;
- cancellation must stop scheduling new work.

Do not optimize prematurely.

---

## 8. Cancellation

Use the project's existing cancellation style where possible.

Cancellation must be checked:

- before expensive work;
- between major analysis stages;
- before writing cache.

A cancelled analysis should not write a partial valid-looking cache entry.

---

## 9. Settings model

Create a minimal runtime settings structure for analysis infrastructure if useful, but do not expose AutoMix UI yet.

Possible fields:

```python
@dataclass(frozen=True, slots=True)
class AutoMixAnalysisSettings:
    provider_id: str = "basic"
    use_cache: bool = True
    max_workers: int | None = None
```

Do not persist a large new project schema in this phase.

---

## 10. Dependency evaluation document

Create/update a short technical note covering candidate analysis backends for Phase 2.

Evaluate at least:

- aubio;
- librosa;
- Beat This!;
- Essentia;
- a lightweight custom/FFmpeg-assisted path.

For each:

- license;
- Python 3.12/Windows status;
- PyInstaller impact;
- binary/native dependencies;
- model size;
- BPM ability;
- beat timestamp ability;
- downbeat ability;
- expected quality;
- suitability as default vs optional.

Do not add all of them.

The purpose is to make the Phase 2 dependency choice deliberate.

---

## 11. Logging

Add concise logs for:

```text
AutoMix cache hit
AutoMix cache miss
AutoMix cache invalidated: reason
AutoMix analysis started
AutoMix analysis completed
AutoMix analysis cancelled
AutoMix analysis failed
```

Do not spam one log line per beat.

---

## 12. Tests

Add tests for at least:

### Model validation

- valid analysis;
- invalid negative BPM;
- invalid confidence;
- unsorted beats;
- beat beyond duration.

### Cache

- save/load;
- cache hit;
- file mtime invalidation;
- file size invalidation;
- schema invalidation;
- analyzer version invalidation;
- malformed JSON recovery;
- atomic/partial-write safety if implemented.

### Service

- cache hit skips provider;
- cache miss calls provider;
- cancellation;
- one failed track does not corrupt other results;
- deterministic mapping by track ID.

Use tiny temporary files. Do not require real copyrighted audio fixtures.

---

## 13. Do not do

Do not implement:

- real BPM detection;
- beat detection;
- downbeat detection;
- transition scoring;
- AutoMix planner;
- FFmpeg crossfade;
- UI;
- model download manager.

---

## 14. Completion criteria

Phase 1 is complete when:

- `app/automix` foundation exists;
- TrackAnalysis is validated and immutable;
- analysis cache is versioned and robust;
- service/provider boundary exists;
- cancellation/progress interfaces exist;
- no legacy path depends on AutoMix analysis;
- targeted tests pass;
- wider regressions pass;
- dependency evaluation is documented.

---

## 15. End-of-phase report

Report:

```text
## Phase 1 Summary
## Files Added
## Files Modified
## Cache Design
## Cache Invalidation Rules
## Provider API
## Cancellation / Progress
## Dependency Evaluation
## Tests
## Regression Results
## Known Limitations
## Phase 2 Readiness
```

Then stop.
