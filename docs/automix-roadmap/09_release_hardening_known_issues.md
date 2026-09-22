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
