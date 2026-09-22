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
