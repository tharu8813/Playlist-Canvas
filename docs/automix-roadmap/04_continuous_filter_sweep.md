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

# Phase 6 — Continuous Filter Sweep DSP

## 목표
현재 3-band 계단식 FILTER_BLEND 대신 cutoff가 시간에 따라 실제로 움직이는 HPF/LPF-style transition을 구현한다. 실제 음악 청감에서 필요성이 확인된 경우에만 진행.

## 조사
bundled FFmpeg의 lowpass/highpass/equalizer/biquad/firequalizer/asendcmd/sendcmd 및 runtime parameter automation 가능성을 실제 executable로 확인.

## 검증 포인트
zipper noise, phase discontinuity, latency, state reset, click/pop, headroom, exact duration, stereo, rate != 1, A→B→C chain.

## A/B
현재 FILTER_BLEND와 새 sweep을 동일 pair에서 비교 가능하게 한다. fallback은 기존 FILTER_BLEND 또는 qsin.

## 금지
stem separation, planner geometry 변경, selector 전체 재작성.

## 권장 commit
`feat: add continuous filter-sweep AutoMix transitions`
