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


---

## Outcome (2026-09-23)

### Schema and generation path (read first, as required)
`TrackAnalysis.vocal_activity` is a tuple of sorted, non-overlapping `(start, end)` source-second spans; empty means "unknown". Before this phase the only producer was `BasicAnalysisProvider`'s voice-band heuristic (1 s hops, 300-3400 Hz energy share >= 0.35), reused unchanged by the Beat This hybrid. Nothing else (Sonara, Beat This) produces vocal data.

### Finding: the heuristic signal is inverted on real music
Against real vocal stems (local StemLab separations of two commercial songs, -20 dB gate on the stem as truth):

| file | voice-band share, sung seconds (median) | non-sung seconds (median) | recall | precision |
|---|---|---|---|---|
| Attention | 0.25 | 0.53 | 0.36 | 0.89 |
| REDRED | 0.13 | 0.68 | 0.06 | 0.50 |
| Attention instrumental | -- | -- (10-12 % flagged as vocal) | -- | -- |

Sung sections are dense full-band arrangements; the quiet intros/breaks between them are what concentrate energy in the voice band. No threshold can repair that, so **basic analyzer v4 no longer guesses vocals** (field stays unknown; Beat This cache version 3). Unknown is never read as "no vocals": it simply cannot trigger VOCAL_SAFE_EQ or the scoring penalty. Effect on the measured real pairs: Attention -> its instrumental, previously VOCAL_SAFE_EQ on a false positive, is now BASS_SWAP.

### Local vocal awareness (active whenever a real detector fills the field)
- `VocalMap`: per side, vocal coverage of each eighth of the window in that side's own source seconds (incoming span = duration x incoming rate).
- Clash = both sides singing together for >= 1/8 of the window (`overlap_ratio`). A line that ends before the next begins ("outgoing vocal ending soon", "incoming vocal starts late") and one-sided vocals are **not** clashes and fall through to the normal rules.
- On a clash VOCAL_SAFE_EQ's mid (voice) swap moves to the handoff point that cuts the least singing (`VocalMap.handoff`, choices 0.25/0.375/0.525/0.625/0.75 of the window, default 0.525 wins ties -> identical render to before). It travels as `AudioRenderTransition.vocal_handoff`; the renderer only shifts the 0.25-wide mid-band fades. Lows keep the bass-swap timing. No ducking: a gain dip under a still-sounding voice risks audible pumping, and nothing measured asked for it.
- Key-clash-only VOCAL_SAFE_EQ keeps the default envelope.
- Diagnostics: `vocal_overlap` is now the overlap ratio (None = unknown) plus `vocal_handoff`; reasons say "vocals overlap for 38% of the window (hand off at 75%)", "vocals hand over without singing together", "vocals on at most one side", or "vocal activity unknown".

### Tests
`LocalVocalTests` (handover, one-sided, unknown, late/early/balanced handoff, key-only) and updated rate-aware/determinism cases in `tests/test_automix_transition_style.py`; `test_vocal_handoff_moves_only_the_vocal_safe_mid_swap` and a real-FFmpeg render with a moved handoff at 1.1x (exact duration +-0.05 s, peak <= 0.98) in `tests/test_automix_renderer.py`; analyzer no longer emits vocal spans.

### Known limitations
Vocal-aware behavior is dormant until a real vocal detector exists -- see Phase 06. The candidate-scoring vocal penalty (`WEIGHT_VOCAL_OVERLAP_PENALTY`) still uses "any vocal on both sides"; it was left alone to keep planner geometry unchanged in this DSP phase and never fires without vocal data.