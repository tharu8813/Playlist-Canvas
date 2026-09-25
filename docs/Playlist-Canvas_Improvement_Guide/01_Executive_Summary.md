# Playlist Canvas 개선 가이드 — Executive Summary

## 0. 감사 범위

- 대상 저장소: `tharu8813/Playlist-Canvas`
- 기준 브랜치: `main`
- 기준 커밋: `cab70df804b6a1885edc100a674566e3377ec1de`
- 감사 일자: 2026-09-25
- 검토 방식:
  - 실제 저장소 구조 및 주요 소스 코드 검토
  - 최근 AutoMix 관련 커밋과 설계 문서 검토
  - Preview / Export / Project Save·Load / AutoMix lifecycle 흐름 추적
  - UI 구조, 디자인 시스템, 접근성 관련 구현 확인
  - 저장소에 기록된 회귀 테스트 결과 및 known issue 문서 검토

> 주의: 이 문서는 현재 저장소 코드를 기반으로 한 정적 코드 감사입니다. 로컬에서 전체 테스트 스위트를 직접 다시 실행한 결과를 의미하지 않습니다. 저장소의 `docs/automix-roadmap/09_release_hardening_known_issues.md`에는 실제 FFmpeg, Beat This, Sonara를 사용한 회귀 실행 결과로 `1070 passed, 4 skipped, 0 failed`가 기록되어 있습니다.

---

## 1. 한 줄 평가

Playlist Canvas는 이미 단순한 개인 프로젝트 수준을 넘어섰습니다. 특히 AutoMix/Preview/Export 영역은 **실제 Windows 파일 핸들, Qt 객체 생명주기, 취소, fallback, 캐시, 동시성까지 방어한 흔적**이 강합니다.

현재 가장 큰 문제는 알고리즘 정확도보다는 다음 세 가지입니다.

1. **UI 스레드에서 수행되는 무거운 프로젝트 로드**
2. **`MainWindow`와 대형 UI 클래스에 남아 있는 과도한 책임**
3. **잘 만든 기능에 비해 뒤처진 문서와 UX 상태 표현**

즉, 엔진을 갈아엎을 시점이 아니라 **잘 돌아가는 엔진을 유지하면서 조종석과 배선을 정리할 시점**입니다.

---

## 2. 현재 잘 되어 있는 부분

### 2.1 AutoMix의 Preview / Export 일관성

현재 설계는 Preview와 Export가 서로 다른 전환 계획을 재계산하지 않고 최종적으로 같은 `CompiledRenderPlan`을 공유하도록 강하게 설계되어 있습니다.

관련 파일:

- `app/controllers/preview_audio_controller.py`
- `app/controllers/progressive_automix_controller.py`
- `app/automix/planner.py`
- `app/automix/renderer.py`
- `app/renderer/ffmpeg_renderer.py`

유지해야 할 원칙:

- Planner가 전환 위치/길이/스타일을 결정
- Renderer는 결정된 계획을 실행
- 같은 입력은 같은 결과
- Preview와 Export의 geometry/DSP 결정 불일치 금지
- 실패 시 앱 전체 실패보다 controlled fallback 우선

### 2.2 Windows/Qt 생명주기 방어

`PreviewAudioController.shutdown()`은 일반적인 `deleteLater()` 사용을 넘어, 실제 Windows `0xC0000409` fail-fast 재현을 바탕으로 다음을 처리합니다.

- `finished` 연결 해제
- 사용자 입력을 제외한 nested event loop
- `wait()`
- `deleteLater()`
- `QCoreApplication.sendPostedEvents(...DeferredDelete)`

### 2.3 저장 안정성

`ProjectService`는 다음을 이미 처리합니다.

- 임시 파일 저장 후 atomic replace
- 실패한 `.tmp` 파일 정리
- `.pvsproj` manifest 크기 제한
- asset 개수 제한
- 임시 디스크 여유 공간 확인
- ZIP 내부 `..` 경로 방어
- stale project cache cleanup

### 2.4 UI 디자인 시스템과 접근성 기반

`docs/STUDIO_DESIGN.md`, `app/ui/design_system.py`, `app/ui/studio.qss` 중심의 공통 디자인 규칙이 존재하고, 여러 위젯에 `setAccessibleName()`, `setAccessibleDescription()`, tooltip, keyboard focus 처리가 이미 들어가 있습니다.

따라서 접근성은 처음부터 다시 만드는 문제가 아니라 **기존 기반의 coverage를 높이는 문제**입니다.

---

## 3. 우선순위 요약

| 우선순위 | 항목 | 유형 | 영향 | 난이도 |
|---|---|---|---|---|
| P1 | `.pvsproj` 로드를 UI 스레드 밖으로 이동 | Logic / UX | 매우 큼 | 중 |
| P1 | `MainWindow`의 런타임 상태를 controller-owned state로 이전 | Architecture | 매우 큼 | 중~큼 |
| P1 | QThread 종료/삭제 패턴 공통화 | Stability | 큼 | 중 |
| P1 | README와 실제 AutoMix Preview 기능 동기화 | Product / Docs | 큼 | 작음 |
| P2 | `main_window.py`, `source_inspector.py`, `export_preview_dialog.py` 분해 | Maintainability | 매우 큼 | 큼 |
| P2 | controller가 `MainWindow` private field에 직접 의존하는 구조 축소 | Architecture/Testability | 큼 | 중~큼 |
| P2 | AutoMix 20~50곡 human listening QA를 릴리즈 게이트로 추가 | Product Quality | 큼 | 중 |
| P2 | Preview 중 “모든 QAction 비활성화”를 mutation-only lock으로 변경 | UX | 중~큼 | 중 |
| P2 | AutoMix fallback/분석 상태를 임시 status bar가 아닌 지속 UI로 노출 | UX | 중 | 중 |
| P3 | Export/Preview/Project의 작업 진행 모델 통합 | UX/Architecture | 중 | 중 |
| P3 | architecture regression 테스트 추가 | Maintainability | 중 | 작~중 |
| P3 | 디자인 문서의 패널 기본 크기 등 stale 값 정리 | Docs | 작~중 | 작음 |

---

## 4. 핵심 발견

### 4.1 P1 — 대형 `.pvsproj` 프로젝트 로드 시 UI 정지 가능

현재 `ProjectController.load_path()`는 progress UI를 갱신한 뒤:

```python
QApplication.processEvents()
document = ProjectService.load(path)
```

를 UI 스레드에서 직접 실행합니다.

`.pvsproj`인 경우 `ProjectService._load_package()`는 ZIP을 열고 포함된 asset을 전부 임시 디렉터리로 복사합니다.

따라서 다음 작업이 GUI thread에 있습니다.

- ZIP manifest read
- asset enumeration
- disk space calculation
- embedded asset extraction
- JSON -> model 복원

대형 프로젝트에서는 Windows가 “응답 없음”처럼 보일 수 있습니다.

**권장:** Save에 `ProjectSaveWorker`를 쓰는 것처럼 `ProjectLoadWorker`를 추가합니다.

### 4.2 P1 — MainWindow 분해가 시작됐지만 상태 소유권은 아직 MainWindow에 남음

현재 이미 `ProjectController`, `PreviewController`, `ExportOrchestrator`, `AutosaveController`, `HistoryController`가 존재합니다.

다만 controller들이 여전히 `self.window._export_frame_staging`, `self.window._project_save_worker`, `self.window._inline_preview` 같은 private 상태를 직접 읽고 씁니다.

즉 메서드는 분리됐지만 **상태와 결합도는 아직 분리되지 않았습니다.**

다음 단계는 controller별 `RuntimeState`를 controller가 직접 소유하도록 옮기는 것입니다.

### 4.3 P1 — QThread lifecycle 해결책이 중복 구현됨

`PreviewAudioController.shutdown()`과 `ProgressiveAutoMixController._stop_thread()`는 거의 같은 중요한 종료 규칙을 구현합니다.

한 곳만 수정되면 다른 곳에서 과거의 Windows crash가 다시 살아날 수 있습니다.

**권장:** 검증된 종료 순서를 `app/utils/qt_worker_lifecycle.py` 같은 단일 유틸로 이동하고 해당 유틸 자체에 regression test를 둡니다.

### 4.4 P1 — README의 AutoMix 설명이 현재 코드보다 뒤처짐

README에는 AutoMix가 “내보내기 오디오에만 적용되며 Preview에는 아직 반영되지 않는다”고 적혀 있습니다.

하지만 현재 저장소에는:

- `ProgressiveAutoMixController`
- partial preview mix
- final export-pipeline mix hot-swap
- Preview/Export plan equality 검증

이 이미 구현되어 있습니다.

기능은 앞으로 갔는데 문서가 뒤에 남아 있습니다.

### 4.5 P2 — 거대 UI 클래스가 유지보수 병목

저장소 tree 기준 파일 크기:

- `app/ui/main_window.py` 약 231 KB
- `app/dialogs/export_preview_dialog.py` 약 167 KB
- `app/inspector/source_inspector.py` 약 162 KB
- `app/canvas/source_item.py` 약 106 KB
- `tests/test_main_window.py` 약 394 KB

파일 크기 자체가 버그는 아니지만 UI 조립, 상태, 비즈니스 흐름, Qt signal orchestration, lifecycle, persistence bridge가 한 객체에 섞이면 변경 영향 범위가 너무 커집니다.

### 4.6 P2 — 테스트를 위해 production import 구조가 굳어 있음

`ExportOrchestrator`와 `PreviewController`는 기존 테스트의 `app.ui.main_window.<Name>` monkeypatch를 유지하기 위해 일부 클래스를 런타임 import합니다.

현재 회귀를 지키기 위한 현실적인 선택이지만 장기적으로는 **테스트가 production architecture를 지배하는 상태**입니다.

**권장:** factory/dependency injection으로 patch target을 안정된 interface에 고정합니다.

### 4.7 P2 — AutoMix는 객관 테스트가 강하지만 청감 검증이 아직 TODO

AutoMix roadmap에는 synthetic test, real FFmpeg, 실제 곡 기반 diagnostic, timing/low-end/loudness metric이 기록되어 있지만 20~50곡 multi-genre human listening panel은 아직 TODO입니다.

오디오 전환의 최종 품질은 수치만으로 완전히 평가할 수 없습니다.

### 4.8 P2 — Preview가 편집을 잠글 때 모든 QAction까지 비활성화

현재 Preview 진입 시 `window.findChildren(QAction)` 전체를 저장한 뒤 모두 disable하고 menu bar/toolbar까지 disable합니다.

안전성은 좋지만 Help, Settings, 일부 View 액션까지 잠겨 앱 전체가 modal 상태처럼 느껴질 수 있습니다.

**권장:** 프로젝트를 변경하는 mutation action만 잠급니다.

---

## 5. 권장 목표 아키텍처

```text
MainWindow
  ├─ WorkspaceView / panel composition
  ├─ ProjectCoordinator
  │    └─ ProjectRuntimeState
  ├─ PreviewCoordinator
  │    └─ PreviewRuntimeState
  ├─ ExportCoordinator
  │    └─ ExportRuntimeState
  ├─ AutosaveCoordinator
  └─ HistoryCoordinator

Domain / Services
  ├─ ProjectService
  ├─ PlaylistService
  ├─ AutoMixWorkflow
  ├─ ExportPolicy
  └─ Renderer

Infrastructure
  ├─ QtWorkerLifecycle
  ├─ FFmpegProcessRunner
  ├─ TempResourceOwner
  └─ CacheStore
```

---

## 6. 3단계 실행 계획

### Phase A — 1~2주

1. `ProjectLoadWorker` 도입
2. project load progress/cancel 지원
3. README AutoMix Preview 설명 수정
4. 디자인 문서 기본 패널 width 동기화
5. Preview action lock을 mutation-only 방식으로 변경
6. AutoMix 상태 chip 추가

### Phase B — 2~6주

1. `ExportRuntimeState`
2. `ProjectRuntimeState`
3. 공통 `QtWorkerLifecycle`
4. `ExportController` → `ExportPolicy` rename
5. factory injection 적용
6. `MainWindow` wrapper를 compatibility layer로 축소

### Phase C — 6~12주

1. `SourceInspector` section 단위 분해
2. `ExportPreviewDialog` playback/backend/panel 분리
3. `SourceItem` rendering/interaction 분리
4. `test_main_window.py` feature suite 분리
5. architecture rule tests 도입
6. AutoMix human listening regression protocol 운영

---

## 7. 완료 판단 기준

- 5GB급 embedded 프로젝트 로드 중 UI event loop가 계속 응답
- 프로젝트 로드 취소가 안전하게 동작
- `MainWindow`가 export/project worker 내부 상태를 직접 가지지 않음
- QThread 종료/삭제 로직이 단일 helper로 통합
- Preview/Export AutoMix plan equality regression 유지
- Preview 중 Help/설정/안전한 navigation 사용 가능
- README/Help/실제 기능 설명이 일치
- AutoMix preset 변경은 human listening report를 근거로 수행
- 기존 전체 회귀 테스트 유지

---

## 8. 결론

가장 피해야 할 선택은 “AutoMix가 복잡하니 전부 다시 짜기”입니다.

현재 AutoMix는 저장소에서 설계 의도가 가장 분명한 영역 중 하나입니다.

개선의 초점은 다음에 두는 것이 가장 효율적입니다.

1. blocking I/O를 UI thread에서 제거
2. controller가 자신의 state와 lifecycle을 소유
3. 검증된 concurrency pattern을 공통화
4. 거대 UI 객체를 기능 단위로 점진적으로 분해
5. 현재 기능을 사용자에게 정확히 설명하고 상태를 보여주기
