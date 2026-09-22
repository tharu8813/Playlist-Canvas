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

# Phase 3 — Progressive AutoMix Preview

## 목표
모든 track 분석과 full mix render가 끝날 때까지 기다리지 않고, 분석 완료된 앞부분부터 AutoMix를 점진 적용한다.

```text
처음부터 Preview 재생 가능
→ 뒤에서 Beat This / Sonara 분석
→ 앞쪽 transition부터 확정
→ 안전한 시점에 hot-swap
→ AutoMix 구간이 뒤로 확장
→ 마지막에는 Export와 동일한 final plan
```

## 요구사항
- 곡별 rhythm+structure 결과가 준비되는 즉시 cache 반영.
- 인접 pair가 준비되면 해당 transition 계획.
- 아직 분석 안 된 tail은 sequential fallback.
- 분석 이벤트마다 전체 playlist를 재렌더하지 말고 debounce/coalesce.
- 현재 playhead 근처 transition을 우선.
- 재생 중인 transition 자체는 가급적 교체하지 않고 safe boundary에서 swap.
- 기존 pending seek / generation token / pause-play 상태 / seekable guard 유지.
- 중간 preview마다 full EBU R128 2-pass 반복 금지. final preview에서 기존 loudness pipeline 사용.
- 최종 progressive plan == normal full compile == Export plan.
- fake progress timer 금지.
- reorder/remove/AutoMix off/project change/preview close 시 stale generation 차단.

## 성능 검증
10/20/50 track synthetic playlist로 analysis event 수, plan update 수, render 수, hot-swap 수, cache-hit churn을 측정. render 수가 analysis event 수와 1:1이 되지 않게 한다.

## 테스트
- A/B 준비 후 A→B만 AutoMix
- C 준비 후 B→C 추가
- unready tail sequential
- rapid analysis events coalesced
- render/hot-swap 중 다음 결과 도착
- 현재 transition 재생 중 unsafe swap 방지
- seek/pause 상태 update
- cache hit playlist
- final progressive == full compile
- final Preview == Export
- cancellation/stale generation blocked

## 금지
Planner geometry 재정의, DSP selector 재설계, stem separation, intermediate AAC 재도입.

## 권장 commit
`feat: progressively apply AutoMix while preview analysis completes`

## 최종 보고
1. incremental architecture 2. partial plan 3. coalescing 4. safe swap 5. loudness 6. final parity 7. 10/20/50곡 수치 8. cache hit 9. cancellation 10. tests


---

## Outcome (2026-09-23)

- **Implementation commit:** `789b73a feat: progressively apply AutoMix while preview analysis completes` (already on `main` when this roadmap started). This phase re-verified it against the spec above and added plan-update counting to the churn simulation.
- **Architecture:** `app/automix/progressive.py` (pure policy: `ProgressiveAnalysis` frontier, `partial_plan`, `divergence_seconds`, `swap_is_safe`, `RenderScheduler`) + `app/controllers/progressive_automix_controller.py` (Qt threads). A partial plan is `compile_automix(all tracks, analyses of the analyzed prefix)`, so every transition it contains is already the final one; the unanalyzed tail stays sequential.
- **Coalescing:** planning runs on every frontier move (a full 50-track compile costs ~3 ms); rendering runs only when an unrendered transition is within `URGENT_HORIZON_SECONDS` of the playhead, after a 0.5 s debounce (2 s max delay), never while another render runs.
- **Safe swap:** `swap_is_safe` refuses a swap at/after the first divergent second and, while playing, inside or 2 s before any transition. Seeks apply the pending mix immediately (audio restarts anyway). The generation token / pending-seek handshake in `ExportPreviewDialog` is unchanged.
- **Loudness:** partial mixes use a linear gain approximating the export loudnorm target (-16 LUFS / -1.5 dBFS ceiling) from per-track `ebur128` measurements, frozen for the run; the final mix is the unmodified export pipeline (2-pass loudnorm, one AAC encode).
- **Parity:** once analysis completes the controller runs `FFmpegRenderer.prepare_playlist_audio` -- the export path -- so the final Preview plan *is* the Export plan (`test_fully_analyzed_partial_plan_is_exactly_the_full_compile`, `test_partial_mix_duration_final_parity_and_level_match` with real FFmpeg).
- **Churn (simulated clock, real planner/scheduler/swap rule, 4 parallel analyses of 15 s each):**

  | tracks | analysis events | plan updates | renders (partial + final) | swaps |
  |---|---|---|---|---|
  | 10 | 10 | 10 | 2 | 2 |
  | 20 | 20 | 20 | 2 | 2 |
  | 50 | 50 | 50 | 2 | 2 |
  | 20, fully cached | 20 | 20 | 1 (final only) | 1 |

- **Tests:** `tests/test_automix_progressive.py`, `tests/test_progressive_automix_controller.py`, progressive cases in `tests/test_main_window.py`; full suite 1024 passed / 1 pre-existing failure (`test_real_video_preview_performance`, Windows `WinError 32`, handled in Phase 09).
- **Known limitations:** editing is locked during Preview, so reorder/remove cannot happen mid-run (a restart of Preview starts a new generation). Partial mixes are FLAC without the 2-pass loudnorm by design.