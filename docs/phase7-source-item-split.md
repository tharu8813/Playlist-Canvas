# Phase 7: SourceItem / Inspector Split (renderer slice 1)

## Scope of this slice

`SourceItem._paint_legacy` is one continuous procedure: a shared preamble
(opacity, fill/gradient, pen, shadow), a long `if`/`elif` chain per
`SourceType`, and a shared epilogue (selection-guide overlay). That shape
makes a type's drawing code cleanly extractable without touching the rest.

`SourceInspector._update_legacy_source_specific_fields` is a different
shape: one flat function that sets visibility for every field in sequence,
each gated by a `source_type is X` condition, not a branch-per-type
dispatch. Splitting it by type safely needs its own redesign (e.g. hide
everything by default, then let each type's editor turn on only its own
fields) rather than a mechanical extraction. That is deferred to the next
Phase 7 slice; this slice covers the renderer side only.

## Changes

- Added `app/canvas/renderers/base.py`: `paint_background(item, painter)`
  and `paint_selection_guide(item, painter, rect)`, extracted verbatim from
  `_paint_legacy`'s shared preamble/epilogue.
- Added `app/canvas/renderers/shape_renderer.py` and
  `progress_renderer.py`: the `SourceType.SHAPE` and `PROGRESS_BAR` drawing
  code, calling the shared base helpers.
- `app/canvas/source_item.py` now registers these two renderers directly
  in `source_registry` instead of the shared `_render_legacy_source`
  adapter. All other 15 types are unchanged.
- `_paint_legacy` keeps every type's code, including SHAPE and
  PROGRESS_BAR: it stays the reference implementation that
  `tests/test_source_registry.py`'s
  `test_registered_rendering_matches_legacy_pixels_for_every_type` compares
  the registered renderer against, for every type, every run. No branches
  were deleted from it.

## Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged --
  this includes the existing pixel-for-pixel comparison of
  `item.paint()` (now dispatching to the new renderers for these two
  types) against `item._paint_legacy()`, for all 17 types.
- `test_main_window.py`: 245 passed.
- Full isolated suite via `scripts/run_tests.py`: 40/41 modules passed;
  the one failure is the already-documented pre-existing
  `test_language_packs` cp949-encoding flakiness, unrelated to this change
  (see `HANDOFF.md`).

## Remaining work

- 15 of 17 SourceTypes still render through `_paint_legacy`. Continue
  splitting types with a similar branch shape (e.g. `BACKGROUND`,
  `ALBUM_COVER`) the same way.
- The Inspector split needs a design pass first: decide how a per-type
  editor function states "everything else is hidden" without repeating a
  long list of `_set_field_visible(..., False)` calls per type, then
  extract type by type behind the existing
  `test_registered_inspector_matches_legacy_fields_and_clears_selection`
  pixel/field-equivalence test the same way the renderer split used
  `test_registered_rendering_matches_legacy_pixels_for_every_type`.
