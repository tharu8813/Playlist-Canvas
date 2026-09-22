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


---

## Outcome (2026-09-23)

### Decision
Implemented as a new, fully rendered and tested DSP style **`TransitionDsp.FILTER_SWEEP`**, but the selector keeps choosing **FILTER_BLEND** for energy-jump / kick-drift windows. This roadmap step is gated on "real listening confirmed a need", and no human listening happened in this run; switching the default is a one-line change in `app/automix/transition_style.py` once someone prefers the sweep in A/B. Rationale (priority order): no regression of an existing, measured-safe style; Preview/Export parity untouched (both render whatever the plan says); the A/B path the spec asks for now exists.

### FFmpeg investigation (bundled n9.0.1, real executable)
- `lowpass`/`highpass`/`biquad` expose `frequency` as a runtime command (`T` flag); `asendcmd` delivers timed commands. Commands must target the full instance name (`highpass@name`); a class name silently did nothing.
- Coefficients update per audio frame, so frames are cut to 10 ms (`asetnsamples=n=480:p=0`; `p=0` so the last frame is never padded -- exact duration).
- Zipper noise, 440 Hz tone swept 20 Hz -> 8 kHz over 8 s, energy above 2 kHz relative to the tone (analysis floor -79.0 dB):

  | transform | 20 ms steps | 5 ms | 1 ms |
  |---|---|---|---|
  | di (default) | -77.8 | -78.9 | -79.0 |
  | tdii / svf | -65.1 | -76.8 | -78.9 |
  | latt | -50.7 | -64.6 | -71.1 |
  | zdf | -73.1 | -78.3 | -78.9 |

  -> direct form I at 10 ms steps (inaudible residual).
- `-filter_complex_script` no longer exists in FFmpeg 9; `-/filter_complex <file>` works. Sweep graphs carry ~1,100 timed commands per side, so they always take the file path added in `c8ab25f` (which also fixed band-DSP graphs over ~33 tracks exceeding the Windows command line).

### Shape
Outgoing: highpass 10 Hz -> 4 kHz over 20-90 % of the window (lows leave first), qsin fade-out over 60-100 %. Incoming: highpass 4 kHz -> 10 Hz over 10-80 % (arrives highs-first), qsin fade-in over 0-40 %. Bass crossover points land at 48 % (incoming arrives) / 52 % (outgoing leaves) -- a short handoff like BASS_SWAP, no bass hole. Overlap summed with the same window limiter as the band styles; clips outside the window are untouched (10 Hz resting cutoff: -0.02 dB at 40 Hz).

### Verification
- Real FFmpeg: A->B->C chain with a 1.05x clip renders to the exact planned duration (+-0.05 s), stereo, peak <= 0.98; the outgoing side's <150 Hz content falls > 30 dB by the end of its sweep; deterministic graph; `transition_dsp=False` fallback renders the plain crossfade.
- Real music A/B (`tools/automix_listening_report.py --ab`, same geometry per style): level/low-end metrics of FILTER_SWEEP within ~0.5 dB of FILTER_BLEND on all four measured windows (e.g. REDRED -> instrumental, 16 s: mean -2.1 vs -2.1 dB, low -2.6 vs -2.2 dB) -- no hole, spike or build-up. Timbre is not measurable this way: **listening TODO**.
- Tests: `tests/test_automix_filter_sweep.py`.

### Known limitations
Needs FFmpeg 7+ (`-/filter_complex`); an older build fails the sweep render and the existing retry renders plain crossfades with identical timing.