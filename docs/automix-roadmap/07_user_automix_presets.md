# 공통 전제

이 작업은 `tharu8813/Playlist-Canvas`의 최신 `main` 기준으로 진행한다.

이미 완료된 핵심 기반은 되돌리지 말 것.

- Beat This 기반 beat/downbeat/BPM 분석
- Sonara 기반 structure/energy 분석
- persistent analysis cache
- AutoMix Planner v2
- effective BPM propagation
- Structure-aware / energy-aware candidate generation
- Commit C.1 exact transition geometry
- source/timeline rate-space consistency
- score saturation fix
- Preview/Export shared `CompiledRenderPlan`
- Preview hot-swap의 pending seek / generation token
- lossless PCM/NUT intermediate
- downstream loudnorm + final AAC 1회 인코딩
- Bass Swap DSP
- VOCAL_SAFE_EQ
- FILTER_BLEND
- SHORT_FADE
- Transition Style Selector
- DSP reasons / diagnostic logging
- DSP runtime fallback to legacy qsin/tri path
- cancellation / stale generation 방어

기존 regression을 깨뜨리면서 새 기능을 얹지 말 것.

## 작업 원칙

1. 실제 코드를 먼저 읽고 현재 구조를 확인한 뒤 수정한다.
2. 보고서보다 실제 source code와 tests를 우선한다.
3. Preview와 Export의 timing/DSP 결정이 서로 갈라지지 않게 한다.
4. Planner는 어디서/얼마나/어떤 style을 결정하고 Renderer는 실행만 담당한다.
5. 같은 입력은 항상 같은 결과를 내야 한다.
6. silent geometry 변경 금지.
7. synthetic test만 통과했다고 실제 음질이 좋다고 주장하지 않는다.
8. known unrelated issue는 이번 단계 범위와 분리한다.

# Phase 9 — AutoMix 사용자 프리셋 / 설정

## 목표
사용자가 내부 알고리즘 수치를 직접 만지지 않고 느낌을 선택할 수 있게 한다.

## 초기 preset
Auto / Smooth / Energetic / DJ 정도로 제한. 실제 tuning 결과를 바탕으로 max tempo change, preferred bars, vocal-safe priority, transition intensity 등에 매핑.

## Architecture
UI 값이 renderer까지 직접 내려가지 않도록 settings에서 immutable resolved config로 정리. old project는 Auto default.

## Preview
preset 변경 시 기존 progressive generation 취소 후 새 generation으로 plan/render. stale swap 방지.

## 테스트
serialization/default, old migration, deterministic plan, running preview에서 switch, Preview==Export, cache reuse, reset.

## 금지
모든 내부 weight 노출, raw FFmpeg option 노출, preset별 planner 복제.

## 권장 commit
`feat: add AutoMix listening presets`


---

## Outcome (2026-09-23)

- **Presets:** `auto` (default), `smooth`, `energetic`, `dj` -- `app.automix.settings.AUTOMIX_PRESETS`, resolved by `resolve_automix_settings(name)` into a frozen `AutoMixTransitionSettings` (unknown/missing -> `auto`). Each preset is only a different set of the planner's existing user-level numbers; there is one planner, and no internal weight, threshold or FFmpeg option is exposed:

  | preset | preferred bars | max tempo change | max transition | fixed-crossfade fallback | effect (measured in tests at 120 BPM) |
  |---|---|---|---|---|---|
  | auto | 8 | 8 % | 20 s | 3 s | 16 s blends (unchanged behavior) |
  | smooth | 16 | 6 % | 32 s | 5 s | 32 s blends, fewer tempo shifts |
  | energetic | 4 | 8 % | 12 s | 2 s | 8 s blends |
  | dj | 16 | 12 % | 32 s | 3 s | beat-matches a 10 % tempo gap that auto/smooth only crossfade |

  Values come from musical conventions (phrase lengths, a DJ's +-8..16 % pitch range), not from listening -- Phase 02 could not run a panel; retune them together with the other listening TODOs.
- **Persistence:** `ProjectSettings.automix_preset` (default `"auto"`); projects saved before presets load as `auto`; an invalid value is normalized to `auto`. Project settings dialog: an "AutoMix style" combo with a one-line description, enabled only while AutoMix is selected.
- **One resolved config, everywhere:** the UI resolves the preset once and passes the `AutoMixTransitionSettings` object down as `automix_settings` -- Preview (`PreviewController` -> `ProgressiveAutoMixController` partial plans and its final `PreviewAudioController`/`prepare_playlist_audio`, and `ExportPreviewDialog`'s own fallback render) and Export (`ExportOrchestrator.prepare_transition_audio`, `RenderWorker` -> `FFmpegRenderer.render`, and the timestamp preparation in `MainWindow`). `None` means `auto`, so every existing caller keeps its behavior.
- **Switching:** editing (including project settings) is locked while Preview runs, so a preset change always takes effect on the next Preview start, which is a new progressive generation (`start()` cancels the previous one; stale results are dropped by the generation token). Analysis caches are preset-independent: switching presets re-plans from cached analyses without re-analyzing.
- **Tests:** `tests/test_automix_presets.py` (resolution, immutability, unknown fallback, deterministic and distinct plans, DJ tempo range, serialization/migration), `test_the_preset_plans_the_partial_mixes_and_reaches_the_final_export_pipeline` (progressive partial plan uses the preset; the final export-pipeline render receives the same object), dialog and Preview wiring in `tests/test_main_window.py`.