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

# Phase 5 — AutoMix Debug / Explainability UI

## 목표
AutoMix가 왜 해당 transition/DSP를 선택했는지 Preview에서 읽을 수 있게 한다. 판단 로직은 변경하지 않는다.

## 표시 정보
A→B, start, duration, strategy, DSP, BPM/rate, structure cue, energy delta, vocal overlap, key compatibility, reasons.

## UI
Preview의 접을 수 있는 AutoMix Details 패널 또는 transition inspector를 우선. developer/debug toggle 가능. Timeline에 transition window와 style 표시 검토. 현재 재생 중 transition highlight. Progressive 상태라면 provisional/final 및 ready-through-track 표시.

## 복사
transition diagnostic을 text/JSON으로 복사 가능하게 검토.

## 성능
CompiledRenderPlan/runtime metadata만 읽고 분석/렌더를 다시 돌리지 않는다.

## 테스트
empty/legacy plan, one/multiple styles, progressive provisional/final, highlight, no-reason fallback, panel reopen.

## 금지
style override, threshold editor, DSP 재렌더 trigger, persisted project format 변경.

## 권장 commit
`feat: add AutoMix transition diagnostics to preview`
