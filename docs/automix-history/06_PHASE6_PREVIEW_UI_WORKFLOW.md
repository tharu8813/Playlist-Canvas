# Phase 6 — AutoMix Preview & UI Workflow

## 0. Goal

Make AutoMix usable without creating a second planning engine.

Add:

- AutoMix enable/disable;
- analysis status;
- transition summaries;
- safe transition preview;
- Preview/Export consistency;
- clear fallback state.

The UI must consume existing AutoMix services and plans.

---

## 1. Core rule

UI does not calculate AutoMix.

Bad:

```text
UI computes BPM / overlap / cue
Export computes it again
```

Good:

```text
AutoMix services/planner
        ↓
CompiledRenderPlan
   ↙            ↘
UI Preview      Export
```

---

## 2. Settings

Add a minimal user-facing AutoMix configuration.

Suggested first-version controls:

```text
[ ] AutoMix

Transition length
Auto / 4 bars / 8 bars / 16 bars

Tempo matching
[✓]

Maximum tempo change
[ 6 % ]

Transition style
Auto / Smooth / Beat Mix
```

Do not expose every internal scoring weight.

---

## 3. Persistence

Persist only user intent/configuration as appropriate.

Do not persist:

- beat arrays;
- downbeat arrays;
- large analysis JSON;
- compiled plans.

Analysis stays cache data.

If project schema must change, use backward-compatible defaults and increment/version only if repository policy requires it.

---

## 4. Analysis workflow

When AutoMix is enabled:

```text
Tracks added/changed
      ↓
Check cache
      ↓
Analyze missing
      ↓
Build/update plan
```

Do not freeze the UI.

Display concise status:

```text
AutoMix analysis 4 / 12
Planning transitions
Ready
```

---

## 5. Transition list UI

Between tracks, expose useful information, for example:

```text
01 Super Shy
   150 BPM

   AutoMix
   8 bars · Beat Mix
   150 → 148 BPM
   [Preview]

02 Whiplash
   148 BPM
```

If the current UI cannot support inline cards cleanly, start with a details panel rather than forcing a large redesign.

---

## 6. Fallback visibility

If a pair falls back:

```text
Smooth crossfade
Reason: beat confidence too low
```

Do not present it as an error unless export cannot proceed.

Developer logs can contain detailed reasons.

---

## 7. Transition preview

Do not build a full realtime dual-deck engine yet.

Recommended:

```text
selected transition
   ↓
render short temporary preview
   ↓
QMediaPlayer
```

Preview should include enough pre/post roll to judge the transition.

Example:

```text
4 seconds before mix
transition
4 seconds after mix
```

Use the same renderer/planned timing semantics as export.

---

## 8. Preview cache

Temporary preview files may be cached by:

```text
track fingerprints
transition plan
settings
renderer version
```

but this is optional.

Always invalidate stale previews safely.

---

## 9. Full playlist Preview

The full existing Preview must use the same `CompiledRenderPlan` that would be used by export.

Do not re-run planner logic independently inside the Preview dialog.

If audio playback cannot yet perform mixed AutoMix in realtime, choose an explicit strategy:

- pre-render a full preview audio master;
- or use a bounded temporary mixed audio render.

Do not show AutoMix visuals over sequential audio.

---

## 10. Presentation timing

During overlap, Canvas should follow PresentationPlan.

Verify:

- album art;
- title;
- artist;
- lyrics;
- Now Playing;
- progress;
- track highlight;
- track-linked video.

No component should independently switch at raw audio clip start unless explicitly intended.

---

## 11. Re-analysis triggers

Reanalyze only when required.

Examples:

Re-analysis:
- media file changed;
- analyzer changed.

Re-plan only:
- transition bars changed;
- max tempo shift changed;
- transition style changed.

No need to redo BPM analysis for planner-only settings.

---

## 12. Cancellation

User can cancel long analysis/preview generation.

UI state must recover cleanly.

No dead disabled buttons after cancellation.

---

## 13. Error behavior

Examples:

### One track analysis fails

Show fallback badge, export still possible.

### Preview render fails

Show preview error, do not necessarily disable export.

### FFmpeg missing

Use the application's established FFmpeg setup UX.

---

## 14. Localization

Add Korean/English strings consistent with existing translator architecture.

Avoid hard-coded mixed-language dialogs.

---

## 15. Tests

Add tests for:

- AutoMix disabled default;
- settings persistence/backward compatibility;
- UI enabling/disabling;
- analysis progress state;
- fallback display state;
- transition preview temp cleanup;
- Preview uses shared plan;
- no duplicate analysis for cached tracks;
- plan-only setting does not force media analysis;
- cancellation restores UI.

Avoid fragile pixel-perfect tests unless repository already uses them.

---

## 16. Do not do

Do not:

- implement key/energy/vocal analysis;
- introduce a realtime low-latency DJ engine;
- duplicate planner logic in widgets;
- make AutoMix mandatory.

---

## 17. Completion criteria

Phase 6 is complete when:

- user can enable AutoMix;
- missing tracks analyze in background;
- transition information is visible;
- transition preview works;
- full Preview timing matches Export plan;
- fallbacks are understandable;
- legacy non-AutoMix UX remains intact.

---

## 18. End-of-phase report

Report:

```text
## Phase 6 Summary
## Settings/UI Added
## Persistence
## Analysis UX
## Transition UI
## Transition Preview
## Full Preview Integration
## Cancellation
## Localization
## Tests
## Known UX Limitations
## Phase 7 Readiness
```

Then stop.
