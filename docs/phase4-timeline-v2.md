# Phase 4: Timeline V2 Models

## Changes

- Added `app/timeline/models.py`: Qt/FFmpeg-independent `AudioClip`, `AudioTrack`,
  `AudioTransition`, `TransitionType`, `Timeline`, and `timeline_from_playlist`.
- Updated `app/models/project.py`: `ProjectDocument.timeline` derives a fresh
  snapshot from the current playlist, including documents loaded from v1/v2.
- Updated the timeline package description; added `tests/test_timeline_models.py`.
- No files or existing features removed.

## Data flow and compatibility

`ProjectDocument.playlist -> timeline_from_playlist -> resolve_track_windows`
produces one audio lane with stable clip IDs, preserving sequential positions,
explicit gaps, clamped starts, zero-length tracks, and disabled entries.
`enabled_only=True` filters before scheduling, matching the existing export policy.
The default duration includes disabled entries, matching the editing timeline.

The derived timeline is not serialized or cached. Access reflects current playlist
edits; an earlier snapshot retains its values. Project version remains 2, and the
legacy playlist remains the sole persisted state. Existing preview/export callers
continue to use the shared scheduler without behavior changes.

Independent clips support overlap, source trim, playback rate and gain. Timeline
duration is the latest clip end across all lanes, not a sum of clip lengths or
the last list element. Transitions describe relationships between existing clips;
their enum values do not imply implemented audio processing.

## Validation

The focused unittest run covers 15 tests across timeline models, the legacy
scheduler, and project persistence. New cases cover overlap, trim/rate, transition
references, disabled filtering, stable IDs, v1/v2 round trips, fresh snapshots,
empty timelines and invalid numeric values. Compilation, MainWindow import, and
an offscreen MainWindow create/show/close smoke check also pass.
The complete isolated suite passed **39/39 modules**, including MainWindow and
FFmpeg streaming integration, on 2026-09-21. Output: `phase4-test-results.log`
(local ignored log). This does not constitute a manual GPU export verification.

Run the complete isolated suite with the working Python 3.12 build environment:

```powershell
$env:PYTHONUTF8 = '1'
.build-venv312/Scripts/python.exe scripts/run_tests.py
```

## Remaining work

V2 clip edits are not yet consumed or persisted by playback/export. Crossfade,
equal-power, beat matching and Automix DSP remain unimplemented, as scoped by
the design. A later render-plan/storage phase must integrate these before an
overlap editor can expose them. Phase 5 (Source Registry) is the next phase.
