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


---

## Outcome (2026-09-23)

### What could and could not be done
- **No human listening was possible** in this run (autonomous agent, no audio output). The only legally held local music was two full songs (`REDRED`, `Attention`, from the user's own StemLab separations -- `Attention` rebuilt by summing its six stems) plus their instrumental/vocal stems. Nothing was committed. The 20-50 song, multi-genre listening panel below remains **TODO for a human**.
- Built instead: an objective, repeatable diagnostic framework that runs the real Export analysis/planning/rendering path and measures each transition window on the rendered mix.

### Diagnostic framework
- `AudioRenderTransition.details` -- the planner now records, per transition, strategy, bars, score, effective/incoming/target BPM, rate, tempo delta, half/double, beat/downbeat confidences, cues, trim, structure anchors, keys and the DSP selector's vocal/key/energy/drift facts (runtime only, never persisted, never read by the renderer).
- `app/automix/diagnostics.py` -- `transition_rows` (one row per junction incl. sequential/gap), `rows_to_csv`/`rows_to_json`, and `window_metrics` (level dip/peak/mean and <150 Hz excess in the window vs 8 s of solo context each side).
- `tools/automix_listening_report.py OUT SONG...  [--ab]` -- analyze (Beat This + Sonara + caches, like Export) -> plan -> render -> `report.csv/json`; `--ab` renders each transition once per DSP style with identical geometry for side-by-side listening and metrics.

### Failures found on real music, and fixes
| # | failure | evidence | change |
|---|---|---|---|
| 1 | **Beat This never ran on AAC/M4A** -- its own file loader (torchaudio/soundfile) cannot decode it, so every such track silently used the basic librosa beats | `REDRED.m4a`: "Could not load audio"; BPM 123.05 (basic) vs 120.0 (Beat This after fix), downbeat confidence 0.30 -> 0.61 | Beat This now receives the FFmpeg-decoded signal (`Audio2Beats`), one decode shared with the basic analyzer (`BasicAnalysisProvider.analyze_signal`). Beat This cache version 1 -> 2. |
| 2 | **Transitions mixed against trailing silence** -- both masters end with 1.5-4 s of digital silence (-45..-67 dB); the 3 s fixed crossfade overlapped only that | window mean level vs context: -13.9 dB before -> -3.4 dB after (Attention -> REDRED); dip -32.5 -> -12.0 dB | `TrackAnalysis.audible_start/end_seconds` (basic analyzer v3, -40 dB below the 90th-percentile 50 ms block; decays measured -18..-33 dB are kept). Tail cues, the bounded downbeat snap and the fixed crossfade use the audible end; incoming cues start at the audible start. Unknown bounds (old data) keep the file edges. Exact geometry (C.1) unchanged: the outgoing clip is trimmed to the candidate's `outgoing_source_out` as before. |
| 3 | **Vocal-activity heuristic is weak on real pop** (not changed here -> Phase 05) | vs vocal stems: recall 0.36 / 0.06, precision 0.89 / 0.50; 10-12 % of seconds of the *instrumentals* flagged as vocal, which picked VOCAL_SAFE_EQ for Attention -> Attention (instrumental) | none yet; recorded for Phase 05/06 |

### Thresholds / envelopes
Not changed: SHORT_FADE 4 s, energy jump 0.30, kick drift 50 ms, all band envelopes, structure/vocal weights. Real beat-matched pairs (REDRED -> instrumental, 16 s BASS_SWAP; Attention -> instrumental, 19.2 s) showed no low-end build-up (low band -0.7..-1.9 dB vs context) and no level spike (+0.5..+3.4 dB peak), i.e. no measured failure to tune against; changing them on two songs would be guessing.

### Remaining TODO (needs a human listener)
- 20-50 legally owned songs across K-pop/EDM/hip-hop/rock/ballad/acoustic, including half/double tempo and long intros/outros; rate timing, bass/vocal clash, energy holes, style choice per transition using `tools/automix_listening_report.py --ab` (procedure and rating sheet: "사람 청감 평가 절차" below).
- Whether the 3 s fixed crossfade for incompatible tempos should be longer/shorter, and whether a quiet intro after a decaying outro (REDRED -> Attention: -24 dB dip even after fix #2) should start the incoming track earlier.

### Tests
`tests/test_automix_diagnostics.py` (rows/CSV/JSON/metrics), audible-bound cases in `tests/test_automix_candidates.py` and `tests/test_automix_basic_analyzer.py`, Beat This tests moved to the signal API.
**Commit:** `fad3a72 refine: tune AutoMix transition selection against real music`. Full suite: 1036 passed, 1 pre-existing failure (`test_real_video_preview_performance`, Phase 09).

## 사람 청감 평가 절차 (Listening panel protocol)

AutoMix 프리셋 값, DSP 선택 임계값, 기본 전환 스타일(예: FILTER_BLEND → FILTER_SWEEP)을
바꾸는 변경은 **이 절차로 만든 평가 결과를 근거로만** 한다. 수치 지표(`report.csv`)는 구멍·과다
저역 같은 명백한 실패를 잡는 용도이고, 음색과 자연스러움은 사람 귀로만 판단한다.

### 곡 선정
- 합법적으로 보유한 20~50곡. 장르별로 섞는다: K-pop, EDM, 힙합, 록, 발라드, 어쿠스틱.
- 꼭 포함할 경우: half/double tempo 쌍, 긴 인트로·아웃트로, 보컬끼리 겹치는 전환, 템포 차이가
  큰 쌍(일반 크로스페이드로 대체되는 경우 확인용).
- 곡 파일과 결과물은 저장소에 커밋하지 않는다.

### 실행
```
python tools/automix_listening_report.py OUT_DIR SONG1 SONG2 ... --ab --preset auto
```
- 평가할 프리셋마다 `--preset`을 바꿔 따로 실행한다(`OUT_DIR`도 분리).
- 결과: `automix_mix.flac`(전체 믹스), `ab/<n>_<style>.flac`(같은 위치·길이, 스타일만 다른 발췌),
  `report.csv`(기계 지표), `ratings.csv`(빈 평가지).

### 평가 (`ratings.csv`, 전환 한 행)
| 열 | 기준 |
|---|---|
| `timing_1to5` | 박자·마디가 자연스럽게 맞물리는가 (5 = 매우 자연스러움) |
| `vocal_clash_1to5` | 두 곡 보컬이 부딪히지 않는가 (5 = 충돌 없음) |
| `low_end_clash_1to5` | 베이스·킥이 뭉개지거나 비지 않는가 (5 = 깔끔) |
| `energy_drop_1to5` | 에너지가 꺼지는 구멍이 없는가 (5 = 끊김 없음) |
| `length_short_ok_long` | 전환 길이: `short` / `ok` / `long` |
| `preferred_ab_style` | `--ab` 발췌 중 가장 좋은 스타일 이름 |
| `comment` | 자유 의견 |

- 전체 믹스를 먼저 이어서 듣고 1~4번을 채운 뒤, 발췌를 들으며 `preferred_ab_style`을 채운다.
- 가능하면 2명 이상이 독립적으로 채운다. 발췌 파일 이름에 스타일이 드러나므로, 다른 사람이
  파일 이름을 가린 채 재생해 주면 편향이 줄어든다.

### 판단 기준 (변경의 근거로 쓸 때)
- 바꾸려는 스타일·값이 같은 전환 묶음에서 평균 점수를 낮추지 않아야 한다.
- 어떤 항목이든 **2점 이하가 새로 생기면** 변경하지 않는다(평균이 올라도).
- `preferred_ab_style`에서 현재 기본 스타일보다 다른 스타일이 과반일 때만 기본값 교체를 검토한다.
- 결과 요약(곡 수, 장르, 항목별 평균, 2점 이하 사례)을 해당 변경의 커밋 메시지나 PR 본문에 적는다.
