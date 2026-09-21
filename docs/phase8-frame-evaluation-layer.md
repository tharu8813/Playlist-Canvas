# Phase 8: Frame Evaluation Layer

## Goal

The design doc asks for a `FrameState` that Preview and Export share, so
the two stop computing "what should the screen show at time t" separately
and risk drifting apart (프롬포트.MD section 23-24, "Preview와 Export가 공유할
FrameState를 만든다").

## What was actually duplicated

Both call sites already funnel through one shared paint routine,
`CanvasSnapshot.capture_track` (`app/preview/canvas_snapshot.py`), used by
both `ExportPreviewDialog` (live scrubbing preview) and
`ExportCanvasCapturer.capture_stream` (real export). So the *pixels*
already came from one code path.

The actual duplication was narrower but real: `ExportCanvasCapturer`
additionally needs a cheap way to tell "did this source's visual state
change since the last sampled frame" to coalesce consecutive identical
frames into one Z-band capture (`_source_state_key`/`_stream_state_key` in
`app/preview/export_canvas_capture.py`). Rather than reuse
`capture_track`'s computation (which mutates live Qt items and paints --
too heavy and side-effecting to call once per candidate frame just to build
a cache key), it re-derived the same timing math from scratch for several
source types. For `SourceType.LYRICS` specifically, this reimplementation
computed the identical cue-index/transition-progress formula independently
of `capture_track`'s inline lyrics branch -- two implementations of one
piece of math, with no test coupling them together. A bug fix or timing
tweak in one could silently drift from the other.

## What this phase does

Introduces `app/preview/frame_state.py`: a pure, dependency-light module
(no Qt imports) holding `resolve_lyrics_cue_state()` and its
`LyricsCueState` result dataclass (`cue_index`, `active_cue_index`,
`transitioning`, `transition_progress`, `cue_start_seconds`). It is lifted
verbatim from `capture_track`'s inline lyrics cue/transition math -- same
formula, same edge cases (offset math, "no successor cue" clamping
`transition_progress` at `1.0` while `transitioning` stays `True`).

Both call sites now resolve the same `LyricsCueState` instead of
duplicating the formula:

- `CanvasSnapshot.capture_track` (`canvas_snapshot.py`) calls it, then
  applies its own downstream logic (easing the raw `transition_progress`
  with `ease_in_out_cubic` for the scroll/fade animation) exactly as
  before -- only the cue/progress *inputs* moved, not the painting.
- `ExportCanvasCapturer._source_state_key` (`export_canvas_capture.py`)
  calls the same function for its `LYRICS` cache-key branch instead of
  re-deriving `active_index`/`cue_index`/`transition` independently. Its
  now-unused `LyricsService` import was removed.

This is the first slice of the Frame Evaluation Layer, scoped to the one
piece of math confirmed to be duplicated with real drift risk. It does not
attempt full unification (returning an injected `FrameState` instead of
`capture_track`'s mutate-then-restore approach on live `SourceItem`/`Source`
objects) -- `SourceItem.paint()` still reads live attributes, and
`capture_track`'s many other branches (text templates, now-playing exit
progress, background cross-fade, animation in/out opacity) are not yet
extracted. See "Remaining work" below.

## Validation

- New `tests/test_frame_state.py` (5 tests): determinism (same input twice
  produces an equal `LyricsCueState`), no-lyrics/no-cue case, transition
  progress over the animation duration (including the "no successor cue"
  clamp-at-1.0 case matching legacy behavior), `subtitle_animation="none"`
  never transitions, and that both timing offsets shift the effective
  elapsed time as before.
- `tests/test_export_frame_equivalence.py`, `test_export_plan.py`,
  `test_functional_regressions.py`, `test_auto_line_counts.py` (118 tests)
  passed unchanged -- these already exercise `capture_track` and the
  export coalescing path pixel/behavior-equivalently.
- `tests/test_main_window.py` passed unchanged.

## Remaining work

- Extract the other branches `capture_track` and
  `ExportCanvasCapturer._source_state_key` independently re-derive: text
  template expansion (already shares `expand_track_template`, lower risk),
  now-playing exit progress, background cross-fade blend fraction,
  animation in/out progress. Each is a smaller version of this same
  pattern: pull the pure formula into `frame_state.py`, have both call
  sites use it, add a determinism test.
- True full `FrameState` unification (a single resolved value object
  `capture_track` applies instead of computing inline, and `SourceItem`
  reads instead of live-mutated attributes) is a larger, higher-risk
  change out of scope for this incremental slice.
