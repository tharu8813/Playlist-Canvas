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

# Phase 4 — 실제 음악 청감 테스트 및 Threshold/DSP 튜닝

## 목표
현재 threshold와 DSP envelope를 실제 음악으로 검증하고 tuning한다.

## 테스트 세트
합법적으로 로컬 보유한 음악 20~50곡 권장. K-POP, EDM, hip-hop, band/rock, ballad, acoustic, BPM 차이 큰 pair, half/double tempo, 긴 intro/outro, vocal-heavy를 포함. repository에 음원을 commit하지 말 것.

## 수집할 diagnostic
from/to, timeline_start, duration, strategy, dsp, effective BPM, tempo delta, beat/downbeat confidence, structure cue, energy delta, vocal overlap, key relation, trim, candidate score, dsp_reasons. CSV/JSON export도 검토.

## 청감 평가
- timing 자연스러움
- beat alignment
- bass clash
- vocal clash
- energy hole/spike
- loudness jump
- harmonic mismatch
- transition length
- style 적절성
- overall preference

## 튜닝 대상
현재 실제 코드의 값을 확인한 뒤 조정: SHORT_FADE 약 4s, energy delta 약 0.3, kick drift 약 50ms, Bass Swap/VOCAL_SAFE_EQ/FILTER_BLEND envelope, structure bonus, vocal penalty 등. 실패 사례와 변경 근거를 연결한다.

## 성공 기준
명백한 beat mismatch/bass clash 감소, vocal-heavy에서 VOCAL_SAFE_EQ 개선, clean BEAT_MATCH에서 BASS_SWAP 선호, synthetic regression 유지.

## 권장 commit
`refine: tune AutoMix transition selection against real music`

## 최종 보고
사용 음악 수/장르, 평가법, 실패 유형, threshold 전후, DSP 전후, 개선/미해결 사례, regressions, 다음 필요 기능.
