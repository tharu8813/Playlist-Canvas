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

## Slice 2 -- now-playing exit progress

`resolve_now_playing_exit_state()` was added the same way: lifted verbatim
from `capture_track`'s inline NOW_PLAYING branch (`visible`/`exit_duration`/
`exit_start`/`exit_progress`), returned as a `NowPlayingExitState(visible,
exit_progress)` dataclass. Both `capture_track` and
`ExportCanvasCapturer._source_state_key`'s NOW_PLAYING branch now call it
instead of re-deriving the same formula -- another confirmed identical
duplicate, same shape of risk as the lyrics one.

### Branches checked and left alone

- **Text template expansion**: both call sites already call the shared
  `expand_track_template()` with the same arguments -- no duplicated
  formula to extract.
- **Background cross-fade blend fraction**: both call sites compute
  `ease_in_out_cubic(elapsed_seconds / fade_seconds)` in one line each --
  trivially small, already using the same shared `ease_in_out_cubic`, not
  worth a wrapper function.
- **Animation in/out progress**: checked and found to be **not a faithful
  duplicate**. `capture_track` additionally branches on whether the source
  has its own timeline window (`source.timeline_start`/`timeline_duration`)
  via `CanvasSnapshot._timeline_window_phase`, using that window's
  phase/progress in place of the passed-in `animation_phase`/
  `animation_progress` when present. `ExportCanvasCapturer._source_state_key`'s
  animation-state cache key only ever uses the passed-in
  `sample.animation_phase`/`animation_phase_duration` -- it never calls
  `_timeline_window_phase`. Unifying these would be a *behavior change* (or
  a confirmation that the export cache key can under-key a source with both
  a timeline window and an animation style, coalescing two visually
  different frames), not a pure refactor -- flagged as a separate follow-up
  investigation rather than folded into this slice.

## Validation

- `tests/test_frame_state.py` (11 tests): determinism for both resolvers,
  lyrics no-cue/transition-progress/animation-none/timing-offset cases, and
  now-playing hidden-after-duration/before-exit-window/exit-progress-ramp/
  exit-duration-clamped/zero-exit-duration cases.
- `tests/test_export_frame_equivalence.py`, `test_export_plan.py`,
  `test_functional_regressions.py`, `test_auto_line_counts.py` (118 tests)
  passed unchanged -- these already exercise `capture_track` and the
  export coalescing path pixel/behavior-equivalently.
- `tests/test_main_window.py` passed unchanged.

## Remaining work

- The animation in/out progress mismatch above needs its own investigation
  (spawned separately) before touching it.
- True full `FrameState` unification (a single resolved value object
  `capture_track` applies instead of computing inline, and `SourceItem`
  reads instead of live-mutated attributes) is a larger, higher-risk
  change out of scope for these incremental slices.
