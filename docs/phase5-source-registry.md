# Phase 5: Source Registry

## Changes

- Added `app/models/source_registry.py`: `SourceDefinition`, `SourceRegistry`,
  and the built-in registration of all 17 `SourceType` values.
- `MainWindow._add_source` resolves the registered component constructor.
- `ProjectDocument` resolves the registered serializer when loading/saving sources.
- `SourceItem.paint` resolves the registered Canvas callback.
- `SourceInspector._update_source_specific_fields` resolves the registered editor
  callback; clearing a selection retains the original no-source behavior.
- Added `tests/test_source_registry.py`. No files or existing features removed.

## Architecture and compatibility

The registry imports only the existing Source model and Python standard library.
Model-only use does not load Qt. Each UI module binds its legacy callback at
module initialization, before its widgets are instantiated. These are internal
bindings, not external plugin discovery or a runtime hot-reload system.

Definitions initially use `Source` as both component and serializer. The existing
Canvas drawing body and Inspector visibility logic are preserved behind adapter
methods. This establishes the dispatch points without combining Phases 5, 6 and 7.
Source creation defaults, source fields, v1/v2 project formats, migration logic,
selection behavior and rendering stay unchanged. Unknown types and duplicate
registrations fail explicitly instead of selecting an unrelated fallback.

The renderer binding covers Canvas drawing (including export captures); it does
not replace the separate FFmpeg video/visualizer overlay implementations.

## Validation

- Before and after the adapter change: 26 existing project/model and export-frame
  equivalence tests passed.
- Seven new test methods cover all type registrations, unknown/duplicate errors,
  legacy migrations and validation, serializer dispatch, v1/v2 round trips,
  and an isolated subprocess proving model imports do not load Qt.
- For all 17 types, registered Canvas output matches the original drawing method
  pixel-for-pixel; image-backed types also exercise a non-empty bitmap.
- All 17 Inspector field-visibility maps and empty selection match the legacy
  method. Replacement callbacks are exercised to prove dispatch is active.
- Compilation and MainWindow import passed. An offscreen create/show/add-text/
  close smoke check verifies that the registered component constructor is used.
- Complete isolated suite: **40/40 modules passed** on 2026-09-21, including
  MainWindow, export frame equivalence and FFmpeg streaming integration. Local
  ignored log: `phase5-test-results.log`. Run with `PYTHONUTF8=1` and
  `.build-venv312/Scripts/python.exe scripts/run_tests.py`.

## Remaining work

Source-specific fields still live in `Source` (Phase 6). Canvas and Inspector
type branches still live in the legacy adapter bodies (Phase 7). Preset/clipboard
code can still call the compatible Source codec directly; schema migration and
codec consolidation remain later cleanup work. This phase does not add external
plugins or new source types, and does not claim manual GPU export verification.

The next phase is Source Component V2.
