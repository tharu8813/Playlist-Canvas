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


---

## Outcome (2026-09-23): implementation deferred

### Why not now
1. **No confirmed conflict that full-mix DSP cannot handle.** The gate for this phase is listening evidence; none was possible in this run. The objective checks that were possible (Phase 02/04, real songs) showed no low-end build-up in beat-matched windows (<150 Hz power -0.5..-2.6 dB vs solo context for BASS_SWAP / FILTER_BLEND / FILTER_SWEEP): the band-split DSP already keeps two bass lines apart.
2. **The real gap is vocal *detection*, not separation for mixing.** Phase 05 showed the only vocal signal the app had was inverted on real music and removed it; vocal-aware DSP is now correct but dormant. A stem model is one way to get vocal activity, but it is the most expensive one.
3. **Cost measured on this machine** (CPU-only, torch 2.14 CPU, local StemLab install of Meta's htdemucs, 84 MB MIT weights): **46 s wall for a 30 s excerpt (~1.5x realtime)** -> ~5 min per 3.5-min song, ~1.5 h for a 20-song playlist. Beat This + Sonara take seconds per song. Stems at that cost cannot sit on the progressive Preview path, and even as background enrichment they would keep a laptop busy for an hour. Roformer-class vocal models are 0.2-1.7 GB and slower.

### Model / licence notes (from the local StemLab provenance file, checked 2026-09-16)
| model | size | licence status |
|---|---|---|
| htdemucs (Meta) | 84 MB | MIT code and weights -- redistributable with notice |
| Mel-Band RoFormer vocals (KimberleyJSN) | 913 MB | model card says MIT, training-data terms not established |
| MDX23C DrumSep | 438 MB | no licence file found -- do not bundle |
All need PyTorch (already the optional Beat This dependency). Weights would be a one-time download into the app's data folder, never an online-only dependency.

### Conditions to start
- A listening panel (Phase 02 TODO) finds vocal-on-vocal or bass clashes that VOCAL_SAFE_EQ/BASS_SWAP audibly fail on, **and**
- either a GPU is common among users or a vocal-activity model an order of magnitude cheaper than full separation is available (a vocal-activity detector only needs "is someone singing", not four clean stems).

### Architecture proposal (for when it starts)
- `app/automix/stems/` with a `StemAnalysisService` beside (not inside) `AnalysisService`/`StructureAnalysisService`: provider protocol, per-track futures, cancel event, same `on_result` callback shape.
- Output first: `vocal_activity` spans only (vocal-stem RMS gate, the same -20 dB relative gate used for ground truth in Phase 05), merged into `TrackAnalysis` at planning time -- the planner and the Phase 05 selector already consume them. Stem audio for bass/kick handoff later, only if listening asks for it.
- Cache: its own store keyed by the existing file fingerprint + provider id + model id + weights SHA256 + implementation version; failures and cancellations never written (no poisoning); size-capped cleanup.
- Scheduling: strictly after rhythm/structure, idle priority, one track at a time, never blocking Preview or Export. Plans compiled before stems exist stay valid; a later plan with vocal data is a new generation (Preview swaps it in with the existing safe-swap rule; Export uses whatever is cached when it starts). Preview/Export parity then holds because both read the same cache state at compile time.
- Resources: CPU fallback always; GPU memory checked before loading; temp stems in the render temp dir, deleted after the RMS pass.