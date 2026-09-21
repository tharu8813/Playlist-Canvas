# Phase 10: Architecture Cleanup

## Goal

프롬포트.MD section 36/50 asks for a final audit pass: circular imports,
unused compatibility adapters, duplicated timeline logic, SourceType
conditional duplication, MainWindow size, renderer coupling, UI → Core
leakage.

## Audit results

| Check | Result |
| --- | --- |
| Circular imports | **Pass.** `compileall`/`import app.ui.main_window` clean. Every deferred (function-body) import checked is a genuine mutual dependency (controllers ↔ `app.ui.main_window`), still needed. |
| Unused compatibility adapters | **Finding, fixed** (see below). |
| Duplicated timeline logic | **Pass.** No `start = max(cursor...)`-style pattern found outside `app/timeline/track_schedule.py`. |
| SourceType conditional duplication | **Minor finding, left alone.** `app/ui/main_window.py` has an `if/elif source_type is SourceType.X` chain for new-source default dimensions/placeholder text. It's one-shot UI-composition-time defaulting, not render/edit dispatch -- doesn't duplicate registry logic. |
| MainWindow size | **4453 lines**, down from the original 6644 (Phase 2). Mostly UI composition + controller wiring; the source-default chain above is the main remaining business-logic-shaped piece. |
| Renderer coupling | **Pass.** No `app/canvas/renderers/*.py` or `app/inspector/editors/*.py` imports `app/ui/`, `QWidget`, `QMainWindow`, or `QDialog`. |
| UI → Core leakage | **Pass, with a naming note.** `app/models/`, `app/timeline/track_schedule.py`, `app/preview/frame_state.py` import no `app.ui`/`QtWidgets`. `app/timeline/timeline_panel.py` does import `QtWidgets` -- it's a legitimate UI panel widget that happens to live in the `app/timeline/` package alongside the Core `track_schedule.py`; not every file under `app/timeline/` is Core, just the scheduling module. |

## Fix: dead legacy-adapter binding loops

`app/canvas/source_item.py` and `app/inspector/source_inspector.py` each
had two passes at module load: first bind every `SourceType` to the shared
legacy adapter (`_render_legacy_source`/`_inspect_legacy_source`), then
immediately rebind each of the 17 types to its Phase 7 dedicated
renderer/editor. Since Phase 7 finished splitting all 17 types (SHAPE
through NOW_PLAYING), the first loop's assignments are always overwritten
before anything reads them -- dead code, though harmless (last-write-wins).

Removed both `for _source_type in SourceType: ...` binding loops. Left
`_render_legacy_source`/`_inspect_legacy_source` and
`_paint_legacy`/`_update_legacy_source_specific_fields` completely
untouched -- the latter two remain the intentional reference
implementations `tests/test_source_registry.py`'s equivalence tests compare
every type's registered output against (per `docs/phase7-source-item-split.md`).

## Findings documented but not changed

- MainWindow's remaining size and the new-source default-value `if/elif`
  chain: consistent with the project's own incremental philosophy --
  splitting further needs a concrete reason, not done speculatively.
- `app/renderer/ffmpeg_renderer.py`'s remaining audio pipeline/encoder/
  process-runner/muxer responsibilities (Phase 9's "Remaining work"):
  several depend on `FFmpegRenderer` instance state, so extracting them
  needs more than the pure-function lift used for `filter_graph.py`.
- Full `FrameState` unification (Phase 8's "Remaining work"): a larger,
  higher-risk change than an incremental slice, deferred until there's a
  concrete need.

## Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged
  -- confirms removing the dead binding loops changed no registered
  renderer/inspector for any of the 17 types.
- `tests/test_main_window.py` passed unchanged.

## Status: Architecture V2 roadmap complete

Phases 1-10 of 프롬포트.MD's roadmap are done. The codebase now has:
controllers under `app/controllers/` (Phase 2), a centralized
`resolve_track_windows`/`playlist_duration` timeline schedule (Phase 3),
Timeline V2 models (`app/timeline/models.py`, Phase 4) with a legacy
playlist adapter, a `SourceRegistry` (Phase 5) driving per-type dispatch,
`SourceComponent` V2 dataclasses with a legacy field adapter (Phase 6), all
17 `SourceType`s on dedicated renderer/editor modules (Phase 7), a Frame
Evaluation Layer with three shared pure state resolvers (Phase 8), a split
FFmpeg filter-graph module (Phase 9), and this cleanup pass (Phase 10).
Remaining deeper work (full `FrameState` unification, further
`ffmpeg_renderer.py` splitting, `render()` decomposition) is deliberately
deferred per the project's "don't restructure without a concrete need"
principle, and is logged in each phase's own doc for whenever that need
arises.
