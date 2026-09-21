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

## Slice 3 -- timeline-window animation phase (bug fix)

The "Animation in/out progress" branch flagged below as a follow-up turned
out to be a real, reachable bug, not just a risk. `source.timeline_start`/
`timeline_duration` and `source.animation_in`/`animation_out` are
independent fields (`app/models/source.py`) -- nothing in the model or the
per-source timing UI keeps a source from having both a timeline window and
a non-`"none"` entrance/exit style at once. When it does,
`CanvasSnapshot.capture_track` correctly paints the window's own
phase/progress (via what was `_timeline_window_phase`), but
`ExportCanvasCapturer._source_state_key`'s cache key only ever reflected
`sample.animation_phase` -- a track-level phase (playlist intro/outro gaps)
that is unrelated to and can be `None` throughout a source's timeline-window
animation. Two export frames with visually different window-animation
progress could resolve to the identical `("stable",)` cache key and get
wrongly coalesced into one frame in the rendered video.

Fixed by lifting `_timeline_window_phase` into `frame_state.py` as
`resolve_timeline_window_phase()` (verbatim logic, no behavior change to
`capture_track`) and wiring `_source_state_key` to call it too, mirroring
`capture_track`'s `source_has_window` branch: when a source has its own
timeline window, both call sites now key/paint off the window's
phase/progress instead of the passed-in `animation_phase`/
`animation_progress`. Regression coverage:
`tests/test_export_frame_equivalence.py::ExportFrameEquivalenceTests::test_timeline_window_animation_changes_export_cache_key`.

### Branches checked and left alone

- **Text template expansion**: both call sites already call the shared
  `expand_track_template()` with the same arguments -- no duplicated
  formula to extract.
- **Background cross-fade blend fraction**: both call sites compute
  `ease_in_out_cubic(elapsed_seconds / fade_seconds)` in one line each --
  trivially small, already using the same shared `ease_in_out_cubic`, not
  worth a wrapper function.

## Validation

- `tests/test_frame_state.py`: determinism for all three resolvers,
  lyrics no-cue/transition-progress/animation-none/timing-offset cases,
  now-playing hidden-after-duration/before-exit-window/exit-progress-ramp/
  exit-duration-clamped/zero-exit-duration cases, and timeline-window
  phase in/out/no-window/no-animation-style cases.
- `tests/test_export_frame_equivalence.py`, `test_export_plan.py` passed
  after the Slice 3 fix -- these already exercise `capture_track` and the
  export coalescing path pixel/behavior-equivalently.

## Remaining work

- True full `FrameState` unification (a single resolved value object
  `capture_track` applies instead of computing inline, and `SourceItem`
  reads instead of live-mutated attributes) is a larger, higher-risk
  change out of scope for these incremental slices.
