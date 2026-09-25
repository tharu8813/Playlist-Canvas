# Playlist Canvas — Logic & Architecture Fixes

## 1. 현재 흐름

```text
MainWindow
 ├─ ProjectController ── ProjectService / ProjectSaveWorker
 ├─ PreviewController
 │   ├─ PreviewAudioController
 │   └─ ProgressiveAutoMixController
 │       └─ PreviewAudioController(final)
 ├─ ExportOrchestrator
 │   └─ FFmpegRenderer
 ├─ AutosaveController
 ├─ HistoryController
 └─ AutoMixAnalysisController
```

이미 MainWindow에서 controller를 추출하는 작업이 진행되었습니다. 이번 개선은 이 방향을 유지하면서 **상태 소유권과 blocking 작업까지 분리**하는 것이 핵심입니다.

---

# 2. [P1] Project Load를 GUI Thread에서 제거

## 문제

### Before

```python
window.activity_progress.begin(
    "project_load",
    "프로젝트 불러오기",
    detail=f"{stage} · {path.name}",
)
QApplication.processEvents()

previous_document = ProjectDocument.from_dict(
    window._project_document().to_dict()
)

document = ProjectService.load(path)
```

`ProjectService.load()`가 `.pvsproj`를 받으면 내부적으로:

```python
with zipfile.ZipFile(target, "r") as archive:
    ...
    required_bytes = sum(info.file_size for info in asset_infos)
    ...
    for info in archive.infolist():
        ...
        with archive.open(info, "r") as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)
```

를 수행합니다.

즉 큰 embedded project의 ZIP 압축 해제와 디스크 I/O가 UI thread에서 진행됩니다.

`QApplication.processEvents()`를 호출해도 다음 blocking call이 끝날 때까지 지속적으로 event loop가 도는 것은 아닙니다.

## 위험

- Windows “응답 없음”
- progress UI 정지
- 취소 불가
- 느린 SSD/동기화 폴더/백신 환경에서 지연 확대

## 개선안

Save와 대칭되는 `ProjectLoadWorker`를 도입합니다.

### After — Worker

```python
# app/services/project_load_worker.py
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.services.project_service import ProjectService, ProjectError


class ProjectLoadWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(path)

    def run(self) -> None:
        try:
            document = ProjectService.load(self._path)
        except ProjectError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"Unexpected project load failure: {exc}")
            return

        self.succeeded.emit(document)
```

### After — Controller

```python
class ProjectController:
    def load_path(self, path: Path) -> bool:
        window = self.window

        if self._load_worker is not None:
            return False

        worker = ProjectLoadWorker(path, window)
        self._load_worker = worker

        worker.succeeded.connect(
            lambda document: self._apply_loaded_document(path, document)
        )
        worker.failed.connect(self._load_failed)
        worker.finished.connect(self._load_finished)

        window.activity_progress.begin(
            "project_load",
            self._tr("프로젝트 불러오기", "Loading project"),
            detail=path.name,
        )

        worker.start()
        return True
```

## 권장 확장 — progress/cancel

```python
from collections.abc import Callable
import threading

ProgressCallback = Callable[[float, str], None]


@classmethod
def load(
    cls,
    path: str | Path,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ProjectDocument:
    ...
```

asset extraction:

```python
total_bytes = sum(info.file_size for info in asset_infos)
processed_bytes = 0

for info in asset_infos:
    if cancel_event is not None and cancel_event.is_set():
        raise ProjectError("Project loading was cancelled.")

    extract_asset(info)
    processed_bytes += info.file_size

    if progress is not None:
        progress(
            processed_bytes / max(1, total_bytes),
            f"Extracting {info.filename}",
        )
```

---

# 3. [P1] MainWindow private-state 결합 축소

현재 `ExportOrchestrator`는 별도 객체지만 export runtime state 대부분을 window에 저장합니다.

### Before

```python
window = self.window

if window._export_frame_staging is None:
    raise RenderError(...)

window._export_capture_count += 1

if window._export_frame_metrics is None:
    window._export_frame_metrics = ExportFrameStagingMetrics()

previous = window._export_frame_cache.get(stream_key)
window._export_frame_index += 1
pipeline = window._export_png_pipeline
```

이 구조는 orchestrator를 독립적으로 테스트하기 어렵고 `MainWindow` private contract를 사실상 public contract로 만듭니다.

## 개선안: ExportRuntimeState

### After

```python
from dataclasses import dataclass, field
from tempfile import TemporaryDirectory
from pathlib import Path


@dataclass(slots=True)
class ExportRuntimeState:
    frame_staging: TemporaryDirectory[str] | None = None
    frame_index: int = 0
    capture_count: int = 0
    frame_cache: dict[str, tuple[object, Path]] = field(default_factory=dict)
    frame_metrics: object | None = None
    png_pipeline: object | None = None
    active_session: object | None = None

    def reset_frames(self) -> None:
        self.frame_index = 0
        self.capture_count = 0
        self.frame_cache.clear()
        self.frame_metrics = None
```

```python
class ExportOrchestrator:
    def __init__(self, host: "ExportHost") -> None:
        self.host = host
        self.state = ExportRuntimeState()
```

---

# 4. [P1] QThread lifecycle 공통화

`PreviewAudioController.shutdown()`과 `ProgressiveAutoMixController._stop_thread()`은 동일한 Windows Qt 생명주기 문제를 다룹니다.

### Before

```python
if running:
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

worker.wait()
worker.deleteLater()
QCoreApplication.sendPostedEvents(
    worker,
    QEvent.Type.DeferredDelete,
)
```

이 패턴은 실제 native crash를 해결한 코드이므로 여러 사본으로 유지하면 위험합니다.

### After

```python
# app/utils/qt_worker_lifecycle.py
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QEventLoop,
    QThread,
)


def stop_qthread_now(worker: QThread | None) -> None:
    """Wait for a cancelled QThread and delete it deterministically."""
    if worker is None:
        return

    try:
        running = worker.isRunning()
    except RuntimeError:
        return

    if running:
        loop = QEventLoop()
        worker.finished.connect(loop.quit)

        try:
            if worker.isRunning():
                loop.exec(
                    QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )
        finally:
            try:
                worker.finished.disconnect(loop.quit)
            except (RuntimeError, TypeError):
                pass

    try:
        worker.wait()
    except RuntimeError:
        return

    worker.deleteLater()
    QCoreApplication.sendPostedEvents(
        worker,
        QEvent.Type.DeferredDelete,
    )
```

controller:

```python
def shutdown(self) -> None:
    self._shutting_down = True
    self.cancel()

    worker, self._worker = self._worker, None
    stop_qthread_now(worker)
```

이 helper에는 Windows 회귀 테스트를 붙여야 합니다.

---

# 5. [P2] nested event loop / processEvents 사용 범위 축소

현재 `QApplication.processEvents()` 또는 `QEventLoop.exec()`가 splash, save wait, project load, export, preview preparation, thread shutdown 등에 사용됩니다.

모두 나쁜 것은 아닙니다.

## 유지 가능한 영역

- startup splash repaint
- 검증된 shutdown wait sequence
- 제한적인 completion wait

## 우선 제거할 패턴

```python
activity.update(...)
QApplication.processEvents()
document = ProjectService.load(path)  # 여전히 GUI thread blocking
```

### Better

```python
worker.progress.connect(activity.update)
worker.succeeded.connect(apply_document)
worker.start()
```

---

# 6. [P2] `ExportController` 명칭 충돌 제거

현재:

```text
app/services/export_controller.py
    class ExportController

app/controllers/export_controller.py
    class ExportOrchestrator
```

첫 번째 클래스는 실제로 stateless policy입니다.

### Before

```python
from app.services.export_controller import ExportController

mode = ExportController.choose_work_mode(width, height, fps)
```

### After

```python
from app.services.export_policy import ExportPolicy

mode = ExportPolicy.choose_work_mode(width, height, fps)
```

`controller`보다 `policy`가 역할을 더 정확하게 설명합니다.

---

# 7. [P2] 테스트 monkeypatch 때문에 production import가 고정되는 문제

현재 일부 controller는 테스트가 `app.ui.main_window.<Name>`을 patch하는 기존 계약을 지키기 위해 런타임 import를 사용합니다.

### Before

```python
def show_export_preview(self, tracks):
    from app.ui.main_window import (
        ExportPreviewDialog,
        FFmpegNotFoundError,
        FFmpegRenderer,
    )

    renderer = FFmpegRenderer(...)
    preview = ExportPreviewDialog(...)
```

## 개선안: factory injection

### After

```python
from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class PreviewFactories:
    renderer: Callable
    dialog: Callable


DEFAULT_PREVIEW_FACTORIES = PreviewFactories(
    renderer=FFmpegRenderer,
    dialog=ExportPreviewDialog,
)


class PreviewController:
    def __init__(self, window, factories=DEFAULT_PREVIEW_FACTORIES):
        self.window = window
        self.factories = factories

    def show_export_preview(self, tracks):
        renderer = self.factories.renderer(...)
        preview = self.factories.dialog(...)
```

테스트:

```python
controller = PreviewController(
    window,
    PreviewFactories(
        renderer=FakeRenderer,
        dialog=FakePreviewDialog,
    ),
)
```

---

# 8. [P2] MainWindow 전체 대신 Host Protocol

### Before

```python
class ProjectController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
```

### After

```python
from typing import Protocol


class ProjectHost(Protocol):
    current_project_path: Path | None
    project_settings: ProjectSettings

    def project_document(self) -> ProjectDocument: ...
    def apply_project(self, document: ProjectDocument) -> None: ...
    def show_project_error(self, error: ProjectError) -> None: ...
```

```python
class ProjectController:
    def __init__(self, host: ProjectHost) -> None:
        self.host = host
```

처음부터 완전하게 바꿀 필요는 없습니다. 자주 사용하는 dependency부터 좁히면 됩니다.

---

# 9. [P2] `.pvsproj` 로드 hardening

현재 ZIP path validation은 꽤 안전합니다.

추가 제안은 보안 취약점 확정 판정이 아니라 **파일 의미와 취소 가능성을 강화하는 hardening**입니다.

## 9.1 duplicate archive entry 거부

```python
seen: set[str] = set()

for info in archive.infolist():
    normalized = PurePosixPath(info.filename).as_posix()

    if normalized in seen:
        raise ValueError(
            f"Duplicate project archive entry: {normalized}"
        )

    seen.add(normalized)
```

## 9.2 cancellation

대형 asset 추출 루프에서 `cancel_event`를 확인합니다.

## 9.3 byte 기반 진행률

파일 수 기반 진행률보다 실제 용량 기반 진행률이 정확합니다.

---

# 10. [P1] AutoMix에서 유지해야 할 invariant

```text
Analysis
  ↓
Candidate generation
  ↓
Planner
  ↓
CompiledRenderPlan
  ↓
Renderer
```

Preview:

```text
Progressive partial plan/render
          ↓
사용자가 먼저 들을 수 있음
          ↓
Final render = export pipeline
          ↓
Hot swap
```

Export:

```text
same analysis/cache
      ↓
same planner/settings
      ↓
same CompiledRenderPlan
      ↓
render
```

## 금지할 리팩터링

```python
# Preview 편의를 위해 별도 전환 geometry 계산
preview_transition = calculate_transition_again(...)
```

대신:

```python
preview.apply_plan(compiled_plan)
```

이어야 합니다.

---

# 11. 권장 architecture tests

## Preview / Export plan equality

```python
assert preview_final_plan == export_plan
```

## UI thread I/O 방지

```python
def test_package_extraction_never_runs_on_gui_thread(qapp):
    gui_thread = QThread.currentThread()
    seen = []

    def hook():
        seen.append(QThread.currentThread())

    # extraction hook ...
    assert all(thread is not gui_thread for thread in seen)
```

## Controller ownership

migration이 끝난 뒤 export runtime field가 MainWindow에 다시 들어오지 못하도록 간단한 architecture test를 둘 수 있습니다.

---

# 12. 적용 순서

1. `ProjectLoadWorker`
2. 공통 `QtWorkerLifecycle`
3. `ExportRuntimeState`
4. factory injection
5. `ExportPolicy` rename
6. `ProjectHost` / `PreviewHost` protocol
7. MainWindow compatibility wrapper 축소

이 순서가 사용자 체감 개선과 regression risk를 가장 잘 균형잡습니다.
