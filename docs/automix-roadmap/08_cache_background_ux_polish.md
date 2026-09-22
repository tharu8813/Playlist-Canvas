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

# Phase 10 — 분석 Cache / Background UX Polish

## 목표
첫 실행/재실행 체감 속도와 상태 표시를 개선한다.

## Cache audit
file identity, analyzer/version/schema identity, invalidation, failure poisoning, corrupt/partial cache, concurrent access.

## Warm-cache UX
불필요한 progress churn을 줄이고 빠르게 preview 준비.

## Background pre-analysis
사용자가 편집하는 동안 idle/background에서 current + next 2 track 우선으로 미리 분석할 수 있는지 검토. CPU 과점유 금지.

## Progress
cache hit / rhythm / structure / planning / rendering / finalizing 실제 stage 기반.

## Optional cache UI
cache size와 clear cache 정도만 검토.

## 테스트
cold/warm/partial/version invalidation/corrupt/cancel/concurrent/cache clear/background prefetch.

## 권장 commit
`refine: improve AutoMix cache and background preparation UX`


---

## Outcome (2026-09-23)

### Cache audit (rhythm `app/automix/cache.py`, structure `app/automix/structure/cache.py`)
| concern | finding |
|---|---|
| file identity | key = canonical path (case-normalized on Windows) + size + mtime_ns; any change is a miss -- OK |
| analyzer/schema identity | provider id + version (Beat This also checkpoint + installed package version) + `SCHEMA_VERSION` in both the key and the envelope -- OK. This roadmap bumped basic 2->4 and Beat This 1->3 for the new decode path, audible bounds and the removed vocal guess, so no stale results are replayed. |
| failure poisoning | exceptions and cancellations are never written; a hybrid provider's fallback result is stored but its provenance mismatch reads as a miss, so a transient Beat This failure is retried -- OK |
| corrupt / partial entries | temp file + fsync + atomic replace; malformed JSON, wrong schema or fields failing `TrackAnalysis` validation are misses -- OK. A process killed mid-write can leave a `*.tmp` orphan: now removed by "Clear". |
| concurrent access | readers see the old or the new entry, never a partial one; on Windows a replace racing a reader can fail and is logged, the result is still returned -- verified by a 6-thread writer/reader test |
| growth | nothing prunes old entries (changed files, old analyzer versions) -> **new**: size + clear in Settings |

### Changes
- **Settings -> General -> AutoMix analysis cache**: "N analyses · X MB" and a **Clear** button (`cache_usage()` / `clear_caches()` over both cache folders; only `*.json` / `*.tmp` are touched; safe any time, results are recomputed on demand).
- **Background pre-analysis** (already existed: the whole playlist, debounced 500 ms after every playlist change, same "auto" analyzer and structure provider as Preview/Export, so it warms exactly the caches they read) now runs **2 files at a time** instead of up to 4, so editing stays responsive. Measured on real songs (4 tracks, 12.4 min of audio, CPU-only): cold rhythm analysis 13.1 s with 2 workers vs 20.0 s with 4 (Beat This inference is serialized anyway, and the 4-worker run included the model load) -- no loss; warm: 0.0 s for rhythm and structure.
- **Preview takes over from background analysis**: starting an AutoMix Preview cancels a still-running background pass (it would analyze the same uncached files a second time and compete for the CPU at the moment the user waits); closing Preview restarts it, which is then mostly cache hits and refreshes the playlist badges.
- **Progress**: the progressive Preview already reports real stages -- "Analyzing i / n · Planning transitions k / n-1", "Preparing preview…" (partial render), "AutoMix ready through track N", "Finalizing AutoMix…" plus the export pipeline's own stage messages -- and with a warm cache it goes straight to the final mix (Phase 01: 1 render). A separate "cache hit" label was not added: a warm playlist finishes analysis in well under a second, so it would only flash.

### Tests
`CacheMaintenanceTests` (usage/clear incl. orphaned temp files and unrelated files, missing folders, concurrent writers/readers) in `tests/test_automix_cache.py`; `test_background_analysis_uses_a_reduced_worker_pool`; Settings cache row and Preview/background handoff in `tests/test_main_window.py`. Existing tests already covered cold/warm, size/mtime/version/schema invalidation, corrupt entries, cancellation and fallback non-caching.