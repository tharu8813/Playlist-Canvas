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

# Phase 11 — AutoMix Release Hardening / Known Issues

## 목표
Windows file lifecycle, Qt flaky, cleanup, error handling을 정리해 release-ready 상태로 만든다.

## Known issue 재현
`test_real_video_preview_performance`의 Windows `PermissionError [WinError 32]`를 최신 main에서 독립 재현. AutoMix DSP 문제라고 가정하지 않는다.

## Audit
QMediaPlayer source release, FFmpeg process 종료, open handles, temp dir cleanup timing, deleteLater/event loop, media source replacement. sleep으로 숨기지 말 것.

## Error surface
analysis/structure/DSP/cache/renderer/preview swap failure의 controlled fallback 또는 사용자 메시지를 확인.

## Cleanup
staging temp, partial NUT, failed outputs, cancelled tasks, old generations.

## Soak
preview open/close, AutoMix on/off, seek, reorder, cancel, export를 반복해 handle/resource leak 검사.

## Release regression
unit/integration/real FFmpeg/Beat This/Sonara/progressive preview/mixed DSP/export/main window 전체 실행.

## 권장 commit
`fix: harden AutoMix preview and temporary-file lifecycle on Windows`


---

## Outcome (2026-09-23)

### Known issue: `test_real_video_preview_performance` WinError 32 -- root cause and fix
Reproduced on its own on latest `main` and on `0394251` (before this roadmap), so not AutoMix. The test body passed; `TemporaryDirectory` cleanup then failed on `moving-pattern.mp4`. Instrumented step by step: the file stayed open after `_stop_preview()`, `close()` and 500 ms of events, and closed only after `SourceItem.release_video_decoder()`. Closing Preview returns a video element to its **editor poster frame** (`reset_video_preview()` -> `_configure_video_preview()`), which keeps its `QMediaPlayer` paused on the file for as long as the element exists -- by design; the app releases it when the element is removed. The fixture deleted the file while the element was still alive. Fix (test only, no sleep): release the element's decoder before the temporary folder goes. Measured separately: an audio or video `QMediaPlayer` releases its file within ~10 ms of `setSource(QUrl())`, so the product's own "stop player, then delete the preview mix folder" order is sound.

Same test file, opt-in soak (`PLAYLIST_CANVAS_RUN_PREVIEW_SOAK=1`), also pre-existing: 0 frames after warm-up. Root cause: the dialog's first refresh queues an FFprobe duration probe on a worker thread; in the test process there are no app settings, so no FFprobe is found and the probe returned 0.0 a few seconds in, overwriting the seeded duration and switching the looping video off. The probe is now pinned to the fixture length before the dialog exists. With `PLAYLIST_CANVAS_PREVIEW_SOAK_BACKEND=cpu` the 20 s soak passes (~24 accepted frames/s, bounded RSS); the default `gpu_layers` backend under `QT_QPA_PLATFORM=offscreen` falls back to CPU with an error banner because there is no OpenGL context -- environmental.

### Lifecycle audit
| area | finding |
|---|---|
| QMediaPlayer release | Preview stops and clears its player before deleting the mix folder; release is immediate (measured). Editor video elements hold their file for the poster frame by design. |
| FFmpeg processes | mix render, analysis decode and export `_run` all terminate (then kill after 2 s) on cancel; only Beat This inference itself is uninterruptible (documented) -- closing the app mid cold analysis took **2.5 s**. |
| temp folders | Preview mix folders, export audio staging and timestamp preparation now use `ignore_cleanup_errors=True`: a transient Windows lock (antivirus/indexer) can leave a stray temp file but can no longer break closing Preview or an export. Partial outputs are unlinked on failure/cancel; the long-graph script lives inside the render temp folder. |
| cancelled / old generations | generation tokens drop late results; `_stop_thread`/`shutdown()` wait out workers before deletion (unchanged, exercised by the soak below). |
| error surface | analysis -> per-track fallback; structure -> none; DSP -> legacy-crossfade retry; cache write -> warning; mix render -> sequential audio; partial preview render -> partials disabled, final still built; final preview mix -> per-track playback with a status message. **New:** an export whose blended mode produced no transitions (failed AutoMix render or nothing blendable) now says so in the export log instead of silently looking like the chosen transitions. |

### Soak / end-to-end (real songs, real FFmpeg + Beat This + Sonara, real `MainWindow`, offscreen, isolated settings and cold caches)
`playlist load -> AutoMix ON -> progressive preview -> seek + pause/resume -> final preview -> close`, then warm cache, reorder, close mid-preparation (cancel), Energetic preset, AutoMix off, export, app close:

| cycle | final mix after | swaps | process handles vs start | temp folders left |
|---|---|---|---|---|
| 1 cold cache, seek + pause/resume | 53.2 s | 1 | +540 (first-use libraries, threads) | 0 |
| 2 warm cache | 30.6 s | 1 | +542 | 0 |
| 3 reordered | 30.9 s | 1 | +544 | 0 |
| 4 closed mid-preparation | -- | 0 | +545 | 0 |
| 5 Energetic preset | 31.1 s | 1 | +547 | 0 |
| 6 AutoMix off | -- | 0 | +548 | 0 |

Handles settle after first use (+1-2 per cycle, no growth trend); no temp folder survives. Export of cycle 5's playlist through `FFmpegRenderer.render`: MP4 728.90 s vs plan 728.89 s, 4 chapters, and **the final Preview plan equals the Export plan**. 12.4 min of audio: ~31 s from Preview start to the final mix on a warm cache, dominated by the export pipeline's mix + 2-pass loudnorm + AAC.

### Other findings recorded
- **Test-harness only:** PySide6 `QTest.qWait` holds the GIL while it sleeps; a cold `librosa` import in a worker pool did not finish in 120 s under `qWait`, but in 1.1 s under `app.exec()` or a `processEvents()` loop. The app is unaffected; long-running threaded tests should pump events instead of `qWait`.
- **Rare native flake:** one `access violation` inside `QAudioOutput` construction in `ExportPreviewDialog.__init__` in ~16 full-suite runs this session; `tests/test_main_window.py` then passed 6/6 back-to-back runs (280 tests each). Qt Multimedia internals, not reproducible on demand; left documented.

### Release regression
Full suite with real FFmpeg, Beat This and Sonara enabled: **1070 passed, 4 skipped (OpenGL/soak opt-ins), 0 failed** -- the long-standing `test_real_video_preview_performance` failure is gone.