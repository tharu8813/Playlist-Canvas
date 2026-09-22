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

# Phase 7 — Vocal-aware DSP 고도화

## 목표
현재 bool 수준의 vocal overlap 판단을 transition 내부의 위치/밀도에 더 민감하게 만든다.

## 먼저 확인
실제 TrackAnalysis.vocal_activity schema와 생성 경로를 읽고 없는 정보를 가정하지 않는다.

## 개선 방향
- outgoing vocal ending soon
- incoming vocal starts late
- midpoint mutual conflict
- one-sided vocal priority
등을 구분. overlap duration ratio / early-mid-late density 등 실제 schema가 허용하는 metric만 사용.

## DSP
VOCAL_SAFE_EQ envelope를 상황별로 다르게 하고 필요 시 소폭 ducking 검토. pumping 금지. unknown vocal data를 no-vocal로 취급 금지.

## 테스트
both/outgoing-only/incoming-only, early-end/late-start, unknown, rate != 1, exact duration/headroom.

## 권장 commit
`refine: make AutoMix vocal-safe transitions locally aware`
