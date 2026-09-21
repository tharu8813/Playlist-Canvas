# Phase 7: SourceItem / Inspector Split

## Slice 1 — renderer (SHAPE, PROGRESS_BAR)

### Scope of this slice

`SourceItem._paint_legacy` is one continuous procedure: a shared preamble
(opacity, fill/gradient, pen, shadow), a long `if`/`elif` chain per
`SourceType`, and a shared epilogue (selection-guide overlay). That shape
makes a type's drawing code cleanly extractable without touching the rest.

`SourceInspector._update_legacy_source_specific_fields` is a different
shape: one flat function that sets visibility for every field in sequence,
each gated by a `source_type is X` condition, not a branch-per-type
dispatch. Splitting it safely needed its own design (see Slice 2 below)
rather than a mechanical extraction, so this slice covers the renderer
side only.

### Changes

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

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged --
  this includes the existing pixel-for-pixel comparison of
  `item.paint()` (now dispatching to the new renderers for these two
  types) against `item._paint_legacy()`, for all 17 types.
- `test_main_window.py`: 245 passed.
- Full isolated suite via `scripts/run_tests.py`: 40/41 modules passed;
  the one failure is a pre-existing `test_language_packs` cp949-console-
  encoding flakiness in `scripts/run_tests.py` under this machine's
  Korean locale, reproduced identically on the pre-refactor code and
  therefore unrelated to this change.

## Slice 2 — inspector (SHAPE, PROGRESS_BAR)

### Design

`_update_legacy_source_specific_fields` has no per-type branch to lift
out, so the split instead introduces `app/inspector/editors/base.py`:

- `TYPE_SPECIFIC_FIELD_KEYS`: every field key the legacy function toggles
  purely by `source_type` (i.e. everything except the always-visible
  shadow group and the toggle-dependent rows `_hide_inactive_dependent_fields`
  already owns independently of type, such as `gradient_start`/`outline_color`).
- `hide_type_specific_fields(inspector)`: sets all of those to `False`.
  A per-type editor calls this first, then shows only the fields it owns.
- `apply_shared_fields(inspector, source)`: the handful of fields every
  type applies the same way -- `text_color` (via the existing
  `_uses_primary_text_color` helper, not tied to one type) and the
  always-visible shadow group.
- `finish(inspector, source)`: the same closing steps the legacy function
  ran (`_hide_inactive_dependent_fields`, `_refresh_property_tabs`).

`app/inspector/editors/shape_editor.py` and `progress_editor.py` each
call `hide_type_specific_fields` → show their own fields (`"shape"`;
`"progress_style"`/`"progress_value"`/`"progress_track_color"`/`"progress_mode"`)
→ `apply_shared_fields` → `finish`. `source_inspector.py` registers these
two directly; the other 15 types stay on the shared
`_inspect_legacy_source` adapter, and
`_update_legacy_source_specific_fields` is untouched (still the reference
`tests/test_source_registry.py`'s
`test_registered_inspector_matches_legacy_fields_and_clears_selection`
compares every type's registered field-visibility dict against).

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged,
  including the field-visibility-dict comparison for all 17 types.
- `test_main_window.py`: 245 passed.
- Full isolated suite via `scripts/run_tests.py`: 40/41 modules passed;
  the one failure is the same pre-existing `test_language_packs`
  flakiness noted above.

## Remaining work

- 15 of 17 `SourceType`s still render and edit through the legacy
  adapters. Continue splitting types with a similar shape (e.g.
  `BACKGROUND`, `ALBUM_COVER`) the same way, on both the renderer and
  inspector sides, behind the two equivalence tests above.
- `SourceItem` and `SourceInspector` still own selection/resize/rotation,
  animation preview wiring, and the shared form-building machinery
  (`_slider_spin_editor`, `_connect_fields`, etc.) -- those stay put;
  Phase 7 only moves the type-specific drawing and field-visibility
  bodies out.
