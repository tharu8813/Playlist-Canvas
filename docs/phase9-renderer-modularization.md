# Phase 9: Renderer Modularization

## Goal

프롬포트.MD section 25 asks to split `app/renderer/ffmpeg_renderer.py`
(1650 lines) into modules with clear responsibilities, explicitly warning
against mechanically chopping the file up for its own sake ("파일을
기계적으로 쪼개는 것이 목표가 아니다. 각각 명확한 책임이 생겼을 때만
분리한다").

## What was analyzed

`ffmpeg_renderer.py` mixes several genuinely different responsibilities:
dataclasses shared across the app (`RenderFrame`, `StaticOverlayLayer`,
`VisualizerOverlay`, etc. -- imported by `app/preview/export_canvas_capture.py`,
`app/dialogs/export_preview_dialog.py`, `app/controllers/export_controller.py`,
`app/ui/main_window.py`, `app/renderer/render_worker.py`), the ~500-line
`FFmpegRenderer.render()` orchestration method, encoder capability/
preflight checks, audio normalization, subprocess invocation, and FFmpeg
filter_complex string construction.

Given how many other modules import the dataclasses from this file, moving
them would ripple across 5+ call sites for no behavioral gain -- out of
scope for an incremental, low-risk slice. The `render()` orchestration
method is the highest-value target long-term but also the highest risk
(the project's explicit "protect the existing Export pipeline" rule), so it
was not touched this slice either.

## What this phase does

Extracts the one cleanly self-contained responsibility: **FFmpeg
filter_complex graph construction**. These were already five `@staticmethod`s
with no `self` access and no I/O -- pure string building from dataclass
inputs to an FFmpeg filter graph string:

- `_python_visualizer_filter_graph`
- `_layered_filter_graph`
- `_video_input_plan`
- `_filter_color`
- `_output_scaling_filter`

Moved verbatim into a new `app/renderer/ffmpeg/filter_graph.py` module
(module-level functions, same bodies, same names minus the leading
underscore). `FFmpegRenderer`'s methods are now one-line delegates to it, so
every existing call site -- including tests that call
`FFmpegRenderer._layered_filter_graph(...)` directly -- keeps working
unchanged, the same "adapter" pattern used for the MainWindow controllers in
Phase 2.

`filter_graph.py` only type-checks against `VisualizerOverlay`/
`VideoClipOverlay`/`VideoFileInput` (via `TYPE_CHECKING`, since
`ffmpeg_renderer.py` uses `from __future__ import annotations`) and defers
its one real import of `VideoFileInput` to inside `video_input_plan()` --
this avoids a circular import (`ffmpeg_renderer.py` imports `filter_graph`
at module scope; `filter_graph.py` never imports `ffmpeg_renderer` at
module scope back).

`ffmpeg_renderer.py`'s now-unused `from math import cos, radians, sin` was
removed (`os` is still used elsewhere in the file and stayed).

## Validation

- `tests/test_functional_regressions.py` (96 tests, including the
  `_layered_filter_graph`/`_python_visualizer_filter_graph`/
  `_video_input_plan` call sites) passed unchanged.
- `tests/test_export_frame_equivalence.py`, `test_export_plan.py`,
  `test_export_session.py`, `test_ffmpeg_streaming_integration.py`,
  `test_release_contract.py` (37 passed, 4 skipped) passed unchanged.
- `tests/test_main_window.py` passed unchanged.

## Remaining work

- The suggested `renderer/ffmpeg/` structure from the design doc
  (`audio_pipeline.py`, `video_pipeline.py`, `encoder.py`, `muxer.py`,
  `command_builder.py`, `process_runner.py`) still has real candidates in
  `ffmpeg_renderer.py`: `_normalize_audio`/`_insert_silence_for_gaps`
  (audio pipeline), `ensure_encoder_available`/`preflight_export`/
  `ensure_encoder_usable`/`direct_encoding_profile`/
  `_video_encoding_arguments` (encoder), `_run`/`_parse_progress_seconds`/
  `_timed_progress_message` (process runner), `_write_export_ffmetadata`/
  `_write_visual_concat`/`_write_concat_file` (muxer/metadata). Each is a
  smaller version of this same pattern -- but several call `self._report(...)`
  or otherwise depend on `FFmpegRenderer` instance state, so extracting them
  needs either passing that state in explicitly or accepting a thin instance
  wrapper, unlike the filter graph functions which needed neither.
- The `render()` method itself (the actual orchestration) is the largest
  and highest-risk piece; per the project's explicit Export Pipeline
  protection rule, splitting it should wait until there is a concrete,
  tested reason to (a real new feature or bug that needs it), not done
  speculatively.
