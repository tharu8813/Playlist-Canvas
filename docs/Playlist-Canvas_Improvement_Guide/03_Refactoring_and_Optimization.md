# Playlist Canvas — Refactoring & Optimization Guide

## 1. 목표

이 문서의 목적은 코드를 단순히 짧고 예쁘게 만드는 것이 아닙니다.

현재 강점인:

- Preview/Export 동작 일치
- Windows resource lifecycle 안정성
- fallback
- 상세 regression tests

를 유지하면서 **변경 비용과 회귀 위험을 줄이는 것**이 목표입니다.

---

# 2. 대형 파일 우선순위

| 파일 | 대략적 크기 | 권장 방향 |
|---|---:|---|
| `tests/test_main_window.py` | 394 KB | feature suite별 분리 |
| `app/ui/main_window.py` | 231 KB | coordinator/state/widget builder 분리 |
| `app/dialogs/export_preview_dialog.py` | 167 KB | playback / transition / embedded UI 분리 |
| `app/inspector/source_inspector.py` | 162 KB | section plugin 구조 |
| `app/canvas/source_item.py` | 106 KB | render/interaction/model adapter 분리 |
| `app/dialogs/lrc_generator_dialog.py` | 94 KB | timing/editor/service 분리 |
| `app/renderer/ffmpeg_renderer.py` | 86 KB | orchestration / command assembly 경계 정리 |
| `app/controllers/export_controller.py` | 78 KB | staging/runtime state/session 분리 |

파일 크기만 보고 억지로 쪼개지 말고 **같이 바뀌는 이유가 같은가**를 분해 기준으로 사용합니다.

---

# 3. `MainWindow` 분해 전략

이미 controller extraction이 진행 중이므로 새로운 거대한 framework를 추가할 필요는 없습니다.

## Step 1 — UI construction 분리

### Before

```python
class MainWindow(QMainWindow):
    def __init__(self):
        ...
        self._build_toolbar()
        self._build_workspace()
        self._build_bottom_panel()
        ...
```

### After

```python
@dataclass(slots=True)
class WorkspaceWidgets:
    toolbar: QToolBar
    left_workspace: QWidget
    canvas_stack: QStackedWidget
    inspector_stack: QStackedWidget
    bottom_tabs: QTabWidget


def build_workspace(
    window: QMainWindow,
    translator: Translator,
) -> WorkspaceWidgets:
    ...
```

`MainWindow`는 결과를 보유하고 signal wiring만 담당합니다.

## Step 2 — Runtime state 분리

```python
@dataclass(slots=True)
class ProjectRuntime:
    dirty: bool = False
    change_serial: int = 0
    save_worker: object | None = None
    save_context: object | None = None


@dataclass(slots=True)
class PreviewRuntime:
    inline_preview: object | None = None
    controls: QWidget | None = None
    track_panel: QWidget | None = None
    lock_state: object | None = None
```

---

# 4. `SourceInspector`를 section registry로 분해

추천 구조:

```text
SourceInspector
  ├─ GeometrySection
  ├─ TypographySection
  ├─ AppearanceSection
  ├─ AnimationSection
  ├─ LyricsSection
  ├─ TrackListSection
  ├─ VisualizerSection
  └─ VideoSection
```

### After

```python
class InspectorSection(Protocol):
    key: str

    def supports(self, source: Source) -> bool: ...
    def build(self, parent: QWidget) -> QWidget: ...
    def bind(self, source: Source) -> None: ...
    def set_language(self, language: Language) -> None: ...
```

```python
class SourceInspector(QWidget):
    def __init__(
        self,
        sections: list[InspectorSection],
        parent=None,
    ):
        super().__init__(parent)
        self._sections = sections

    def show_source(self, source: Source) -> None:
        for section in self._sections:
            supported = section.supports(source)
            section.widget.setVisible(supported)
            if supported:
                section.bind(source)
```

장점:

- 새 `SourceType` 추가 시 giant inspector 수정 범위 감소
- section 단위 unit test
- focus/accessibility 책임 분리
- translation 변경 범위 감소

---

# 5. `ExportPreviewDialog` 분해

추천 구조:

```text
ExportPreviewDialog
  ├─ PreviewPlaybackController
  ├─ PreviewAudioTransitionController
  ├─ PreviewTimelineModel
  ├─ PreviewTrackPanel
  └─ PreviewSurfaceHost
```

Dialog는 조립과 표시를 담당하고 playback/lifecycle은 controller에 둡니다.

---

# 6. QThread worker를 만능 base class로 만들지 말 것

피해야 할 방향:

```python
class UniversalWorker(QThread):
    # save, load, ffmpeg, analysis, update, preview를 전부 처리
```

공통화할 것은 업무가 아니라 lifecycle primitive입니다.

```python
def stop_qthread_now(worker): ...
```

각 worker는 계속 자기 업무만 담당해야 합니다.

---

# 7. Context 객체로 private field 접근 축소

### Before

```python
class ExportOrchestrator:
    def __init__(self, window):
        self.window = window
```

### After

```python
@dataclass(frozen=True)
class ExportEnvironment:
    translator: Translator
    settings_service: AppSettingsService
    playlist_service: PlaylistService
    source_store: SourceStore
    canvas: LiveCanvas
```

```python
class ExportOrchestrator:
    def __init__(
        self,
        environment: ExportEnvironment,
        ui: ExportUiPort,
    ) -> None:
        self.environment = environment
        self.ui = ui
        self.state = ExportRuntimeState()
```

---

# 8. FFmpeg process 실행 정책 공통화

AutoMix loudness 측정 등에는 다음 정책이 반복됩니다.

- timeout
- terminate
- grace wait
- kill
- stderr decode
- cancellation polling

### After

```python
@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def run_cancellable_process(
    args: list[str],
    *,
    cancel_event: threading.Event,
    timeout_seconds: float,
    kill_grace_seconds: float = 2.0,
) -> ProcessResult:
    ...
```

적용 후보:

- loudness probe
- ffprobe
- renderer preflight
- encoder validation

단, 장기 streaming pipe까지 억지로 하나의 helper에 넣지는 않는 것이 좋습니다.

---

# 9. Cache key 타입 강화

```python
@dataclass(frozen=True, slots=True)
class AnalysisCacheKey:
    file_identity: str
    provider: str
    model_version: str
    schema_version: int = 1

    def serialize(self) -> str:
        return (
            f"v{self.schema_version}:"
            f"{self.provider}:"
            f"{self.model_version}:"
            f"{self.file_identity}"
        )
```

분석 provider/model 교체 시 stale cache 오염을 줄일 수 있습니다.

---

# 10. `except Exception` 정책

Preview/AutoMix의 broad catch를 무조건 제거하면 안 됩니다. 이 영역은 failure를 fallback으로 변환하는 boundary이기 때문입니다.

## 허용

```python
try:
    analysis = provider.analyze(...)
except Exception as exc:
    LOGGER.warning("Analysis failed: %s", exc)
    return fallback
```

## 피해야 함

```python
try:
    ...
except Exception:
    return None
```

## 권장

```python
except Exception as exc:
    LOGGER.exception(
        "Progressive structure analysis failed",
        extra={
            "generation": self._generation,
            "track_count": len(self._tracks),
        },
    )
    self._degrade_to_no_structure()
```

---

# 11. 상태 문자열 대신 Enum/dataclass

### Before

```python
self.progress.emit(
    "Finalizing AutoMix",
    fraction,
    message,
)
```

### After

```python
from enum import Enum


class PreviewStage(Enum):
    ANALYZING = "analyzing"
    PARTIAL_RENDER = "partial_render"
    FINALIZING = "finalizing"
    READY = "ready"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class PreviewProgress:
    stage: PreviewStage
    fraction: float
    detail: str = ""
```

번역은 View layer에서 수행합니다.

---

# 12. Export frame reuse 최적화는 계측 후

현재 export staging은 동일 QImage를 재사용합니다.

```python
previous = window._export_frame_cache.get(stream_key)

if previous is not None and image == previous[0]:
    ...
```

이는 좋은 최적화입니다.

추가 최적화 전에 다음을 계측합니다.

- capture FPS
- identical-frame reuse ratio
- average QImage comparison time
- PNG fallback 비율
- streamed visual 비율
- staging queue peak
- temp bytes/output minute

실제 profile에서 image equality 비용이 유의미할 때만 hash/damage tracking을 검토합니다.

---

# 13. Project package load 최적화

우선순위는 다음입니다.

1. GUI thread에서 제거
2. byte 기반 progress
3. cancellation
4. 이후 필요할 때 동일 package 재오픈 cache 재사용

cache correctness가 성능보다 우선입니다.

---

# 14. Test suite 구조 개선

`tests/test_main_window.py`는 기능별로 분리하는 것이 좋습니다.

```text
tests/
  ui/
    test_workspace_layout.py
    test_keyboard_shortcuts.py
    test_inline_preview_mode.py
    test_project_actions.py
  project/
    test_project_load_flow.py
    test_project_save_flow.py
    test_project_recovery.py
  export/
    test_export_orchestration.py
    test_export_ui_lock.py
  automix/
    ...
```

이동 순서:

1. 새 파일로 이동
2. 동일 테스트 수/coverage 확인
3. patch target 정리
4. factory injection 전환
5. legacy test container 제거

---

# 15. Architecture regression tests

## service → main_window 역참조 방지

migration 완료 후:

```python
def test_services_do_not_import_main_window():
    ...
```

## giant file warning budget

강제 실패보다 warning/report가 적절합니다.

```python
MAX_WARN_LINES = {
    "app/ui/main_window.py": 4000,
    "app/dialogs/export_preview_dialog.py": 2500,
}
```

---

# 16. 구조적 typing 우선순위

1. AutoMix plan/model
2. RuntimeState dataclass
3. worker result
4. progress event
5. Host Protocol
6. factory contracts

Qt widget local 변수까지 과도하게 annotation하는 것은 후순위입니다.

---

# 17. Logging context

권장 공통 필드:

- session/generation
- track id
- transition mode
- AutoMix preset
- worker name
- operation duration
- fallback reason

```python
LOGGER.info(
    "Preview partial render ready",
    extra={
        "generation": generation,
        "track_count": track_count,
        "covered_until": covered_until,
    },
)
```

파일 경로는 crash report 공유 시 privacy를 고려해 redaction 정책을 둘 수 있습니다.

---

# 18. 리팩터링하지 말아야 할 것

현재 그대로 유지할 가치가 큰 것:

- AutoMix planner/renderer 역할 분리
- Preview/Export shared final plan
- generation-token stale result 방어
- `ignore_cleanup_errors=True`를 이용한 Windows transient lock 완화
- atomic project save
- controlled fallback chain

또한 `SourceInspector`를 한 번에 JSON schema 기반 만능 폼 엔진으로 재작성하는 것도 권장하지 않습니다.

---

# 19. 완료 조건

- 각 controller가 자기 worker/temp resource를 소유
- controller가 `MainWindow` private field 수십 개를 건드리지 않음
- 테스트가 `app.ui.main_window.<class>` patch에 의존하지 않음
- 공통 QThread lifecycle이 단일 구현
- Project load는 GUI thread에서 disk extraction을 하지 않음
- `ExportPreviewDialog`와 `SourceInspector`가 기능 컴포넌트로 분리
- Preview/Export 결과는 기존과 동일

이 프로젝트에서는 **동작을 바꾸지 않는 구조 개선 자체가 큰 성과**입니다.
