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

## Slice 3 — BACKGROUND, ALBUM_COVER (renderer + inspector)

These two types share their image-drawing code with several other
image-backed types (`IMAGE`, `LOGO`, `WATERMARK`, `VIDEO`), so the split
needed one more shared helper on each side rather than being self-contained
like SHAPE/PROGRESS_BAR:

- `app/canvas/renderers/base.py` gained `paint_image_content(item, painter,
  rect, frame_style)`, extracted verbatim from `_paint_legacy`'s shared
  `image_backed_types` branch (`frame_style` is what the legacy branch
  special-cased only for `ALBUM_COVER`; `background_renderer.py` always
  passes `"rounded"`). `background_renderer.py`/`album_cover_renderer.py`
  each call `paint_background()`, then `paint_image_content()` when the
  item has a pixmap or their own non-image fallback drawing (copied
  verbatim from the old `elif SourceType.BACKGROUND`/`ALBUM_COVER`
  branches) when it doesn't, then `paint_selection_guide()`.
- `app/inspector/editors/base.py` gained `apply_image_backed_fields(inspector,
  source, *, show_file)`: the `image_fit`/`blur`/`brightness`/`contrast`
  fields every `IMAGE_BACKED_TYPES` member shows the same way, plus `file`
  (whose own visibility condition differs per type -- `BACKGROUND` gates it
  on `background_mode == "image"`, `ALBUM_COVER` always shows it).
  `background_editor.py`/`album_cover_editor.py` call it plus their own
  remaining fields (`background_mode`/`background_ambient`/
  `background_track_transition(_seconds)`; `album_frame`).
- `source_item.py`/`source_inspector.py` register these two renderers/editors
  directly; `_paint_legacy` and `_update_legacy_source_specific_fields`
  are unchanged, still the reference implementations the equivalence tests
  compare against for all 17 types.

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged.
- `tests/test_main_window.py` passed unchanged.

## Slice 4 — IMAGE, LOGO, WATERMARK, VIDEO (renderer + inspector)

### Renderer

`IMAGE`, `LOGO`, and `WATERMARK` have no `elif` branch of their own in
`_paint_legacy` -- when they have no pixmap loaded, execution falls all the
way through the `elif` chain to its trailing `else`, which is a **generic
name/text placeholder shared by every unmatched type** (this is also how
`TEXT` and `TIME` render, though those two are unsplit and still go through
`_paint_legacy` as a whole). Missing this on the first pass caused
`test_registered_rendering_matches_legacy_pixels_for_every_type` to fail for
these three types with a default (no file) source -- caught by the test as
intended.

Fixed by extracting that trailing `else` into
`app/canvas/renderers/base.py::paint_generic_fallback(item, painter, rect)`
(rounded rect + word-wrapped name/text, with the `TEXT`/`TRACK_LIST`
overflow handling and outline-color special case kept verbatim). Now:

- `image_renderer.py`/`logo_renderer.py`/`watermark_renderer.py`: pixmap
  present -> `paint_image_content(..., "rounded")`; otherwise ->
  `paint_generic_fallback`.
- `video_renderer.py`: pixmap present -> same `paint_image_content`;
  otherwise -> the dark placeholder + play icon + filename, copied verbatim
  from the old `elif SourceType.VIDEO` branch (this type already had its
  own branch, so no fallback helper was needed here).

### Inspector

All four are in `IMAGE_BACKED_TYPES`, so they use
`apply_image_backed_fields()` from Slice 3. `VIDEO` is the one exception the
legacy code carves out of `file` visibility (its own `video_settings` field
covers file selection instead), so `video_editor.py` passes
`show_file=False` and additionally shows `video_settings`. The other three
pass `show_file=True` and have no fields of their own beyond what
`apply_image_backed_fields`/`apply_shared_fields` cover.

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed, including
  the pixel comparison for a no-pixmap `IMAGE`/`LOGO`/`WATERMARK` source
  that caught the missing fallback above.
- `tests/test_main_window.py` passed unchanged.

## Slice 5 — TEXT, TIME (renderer + inspector)

### Renderer

As predicted in Slice 4: `TEXT` and `TIME` never had a branch of their own
in `_paint_legacy` -- the generic trailing `else` *is* their renderer, in
full. `text_renderer.py`/`time_renderer.py` are now just
`paint_background()` -> `paint_generic_fallback()` -> `paint_selection_guide()`,
reusing the helper Slice 4 already extracted; no new drawing code needed.

### Inspector

Both are in the legacy function's `text_types` set, so they share `text`,
`font_size`, `font_weight`, `font_family`, `text_stroke_color`,
`text_stroke_width`, and `text_alignment`. `text_overflow` is visible only
for `{TEXT, TRACK_LIST}`, so `text_editor.py` includes it in its own field
list and `time_editor.py` does not -- the only difference between the two
editors.

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged.
- `tests/test_main_window.py`: 245 passed, 19 subtests.

## Slice 6 — AUDIO_VISUALIZER, AUDIO_WAVEFORM (renderer + inspector)

Both were self-contained `elif` branches in `_paint_legacy`, like
Slices 1-2, so this was a mechanical extraction with no new shared helper:

- `audio_visualizer_renderer.py`/`audio_waveform_renderer.py`: copied
  verbatim from their `elif` branches, calling `paint_background()`/
  `paint_selection_guide()` around the same drawing code (bar/line/arc
  style dispatch for the visualizer; the sine-sampled path for the
  waveform).
- `audio_visualizer_editor.py` shows its 12 `visualizer_*` fields;
  `audio_waveform_editor.py` shows only `waveform_style` -- matching the
  legacy code exactly (the waveform renderer reads `visualizer_bars`/
  `visualizer_line_width` too, but the Inspector never exposes those for
  this type, so they stay at their defaults for `AUDIO_WAVEFORM` sources).

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged.
- `tests/test_main_window.py`: 245 passed, 19 subtests.

## Slice 7 — AUDIO_LEVEL_METER, PARTICLE_OVERLAY (renderer + inspector)

As anticipated, both `elif` branches already delegated their drawing to
existing helper functions (`app.utils.level_meter_painter.paint_level_meter`,
`app.utils.particle_painter.paint_particles`), so this slice was a
mechanical extraction:

- `audio_level_meter_renderer.py`/`particle_overlay_renderer.py` copy the
  branch verbatim (arg assembly + the helper call) between
  `paint_background()`/`paint_selection_guide()`.
- `audio_level_meter_editor.py`/`particle_overlay_editor.py` show their
  17/12 own field keys (identical to the `for key in (...)` loops in
  `_update_legacy_source_specific_fields`), then `apply_shared_fields`/
  `finish` as usual.

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged.
- `tests/test_main_window.py`: 245 passed, 19 subtests.

## Slice 8 — LYRICS, TRACK_LIST, NOW_PLAYING (renderer + inspector)

The final three types, completing all 17 `SourceType`s.

### Renderer

- `lyrics_renderer.py`: the longest and most stateful branch in
  `_paint_legacy` -- per-line entrance/leaving alpha and blur driven by
  `item._subtitle_*` transition state, ghost-pixmap caching via
  `item._lyric_ghost_pixmap`, and font selection via `item._lyric_fonts`.
  Copied verbatim rather than restructured, specifically so the pixel
  equivalence test is the thing verifying it, not a read-through.
- `track_list_renderer.py`: `self._paint_track_list` was already a
  standalone method outside the `elif` chain, so this is a two-line
  wrapper (`paint_background()` -> `item._paint_track_list(...)` ->
  `paint_selection_guide()`).
- `now_playing_renderer.py`: copied verbatim from its `elif` branch
  (style-dependent card color/pen, then label/title/detail text blocks).

### Inspector

All three are in `text_types`, sharing `text`/`font_size`/`font_weight`/
`font_family`/`text_stroke_color`/`text_stroke_width`/`text_alignment`.
`lyrics_editor.py` adds its `subtitle_*` fields; `track_list_editor.py`
adds `text_overflow` (the other member of the `{TEXT, TRACK_LIST}`
overflow set) plus its `track_list_*` fields; `now_playing_editor.py` adds
its `now_playing_*` fields.

### Validation

- `tests/test_source_registry.py` (7 tests, 57 subtests) passed unchanged
  -- notably including `LYRICS`'s pixel comparison, which is the real
  check on the verbatim copy above.
- `tests/test_main_window.py`: 245 passed, 19 subtests.
- Full isolated suite via `scripts/run_tests.py`.

## Status: complete

All 17 `SourceType`s now render and edit through dedicated
`app/canvas/renderers/*.py` / `app/inspector/editors/*.py` modules
registered in `source_registry`, instead of the shared
`_render_legacy_source`/`_inspect_legacy_source` adapters. `_paint_legacy`
and `_update_legacy_source_specific_fields` are intentionally left in place
as the reference implementations `tests/test_source_registry.py` compares
every type's registered output against; they are candidates for deletion
in a later cleanup phase once that safety net is no longer needed.

`SourceItem` and `SourceInspector` still own selection/resize/rotation,
animation preview wiring, and the shared form-building machinery
(`_slider_spin_editor`, `_connect_fields`, `_paint_track_list`, lyric/text
helpers, etc.) -- Phase 7's scope was only the type-specific drawing and
field-visibility bodies.
- `SourceItem` and `SourceInspector` still own selection/resize/rotation,
  animation preview wiring, and the shared form-building machinery
  (`_slider_spin_editor`, `_connect_fields`, etc.) -- those stay put;
  Phase 7 only moves the type-specific drawing and field-visibility
  bodies out.
