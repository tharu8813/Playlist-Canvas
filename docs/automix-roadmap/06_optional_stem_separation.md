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

# Phase 8 — Optional Stem-aware AutoMix

## 선택 단계
실제 청감 결과에서 full-mix DSP로 해결하기 어려운 bass/vocal conflict가 충분히 확인된 경우에만 진행한다.

## 조사
최신 local open-source stem model의 license, size, CPU/GPU/NPU, Windows, Python version, speed, quality, redistribution 가능성을 검증.

## Architecture
TrackAnalysis에 억지로 넣지 말고 별도 StemAnalysisService/cache를 우선 검토. source identity + model identity/version 기반 cache. 실패 결과 cache poisoning 금지.

## Fallback
stem 준비 전에는 기존 full-mix AutoMix DSP로 동작. Preview를 stem 때문에 막지 않는다.

## use cases
bass stem swap, vocal conflict priority, drum/kick handoff.

## resource
GPU memory, CPU fallback, cancel, temp files, cache size/cleanup.

## 금지
stem 없으면 AutoMix 실패, online-only dependency, license 불명 모델 bundling.

## 권장 commit
`feat: add optional stem-aware AutoMix transitions`
