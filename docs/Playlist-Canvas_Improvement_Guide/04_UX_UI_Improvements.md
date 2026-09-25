# Playlist Canvas — UX/UI Improvements

## 1. UX 감사 관점

현재 UI는 이미 다음 기반을 갖고 있습니다.

- 다크 전용 studio theme
- 좌측 Source / 중앙 Canvas / 우측 Inspector
- 하단 Timeline/Playlist/Preview workspace
- 공통 QSS/design system
- keyboard focus 상태
- accessible name/description
- 진행 상태 widget
- Preview 중 편집 잠금

따라서 화려한 리디자인보다 **기다림, 상태 인지, 오류 복구, 작업 모드 전환, 기능 발견성**을 우선 개선하는 편이 효과적입니다.

---

# 2. [P1] 큰 프로젝트 열기 UX

## 현재

1. `.pvsproj` 선택
2. “프로젝트 불러오기” progress 표시
3. `QApplication.processEvents()`
4. ZIP/embedded media extraction이 GUI thread에서 동기 실행
5. 큰 파일이면 창이 멈춰 보일 수 있음

## 목표

```text
[프로젝트 불러오는 중]
project-name.pvsproj

✓ 프로젝트 정보 읽기
● 포함 미디어 풀기      1.8 GB / 4.2 GB
○ 누락 미디어 확인
○ 작업공간 적용

[취소]
```

단계:

- Reading manifest
- Extracting media
- Validating media
- Applying workspace

byte 기반 진행률을 권장합니다.

---

# 3. [P2] Preview 중 앱 전체가 잠긴 느낌 완화

현재:

```python
actions = tuple(
    (action, action.isEnabled())
    for action in window.findChildren(QAction)
)

for action, _enabled in actions:
    action.setEnabled(False)

window.menuBar().setEnabled(False)
window.toolbar.setEnabled(False)
```

의도는 프로젝트 mutation 방지이지만 Help/Settings/View 계열 액션까지 함께 잠길 수 있습니다.

## 개선안: mutation lock

```python
MUTATING_ACTION_KEYS = {
    "new_project",
    "open_project",
    "add_source",
    "delete_source",
    "paste",
    "undo",
    "redo",
    "project_settings",
    "export",
}


def lock_editor_mutations(window) -> dict[QAction, bool]:
    state = {}

    for key in MUTATING_ACTION_KEYS:
        action = window.actions[key]
        state[action] = action.isEnabled()
        action.setEnabled(False)

    window.canvas.setEnabled(False)
    window.setAcceptDrops(False)
    return state
```

유지 가능한 액션:

- Help
- About
- shortcuts
- log folder
- 일부 Settings
- panel visibility
- window controls

---

# 4. [P2] AutoMix 상태를 Preview 가까이에 지속 표시

현재 `activity_progress`와 status bar를 통해 background mix 상태를 보여주는 구현은 좋은 편입니다.

다만 사용자는 계속 다음을 궁금해합니다.

- 지금 일반 곡별 재생인가?
- partial AutoMix인가?
- 최종 mix가 준비됐나?
- fallback 됐나?
- 몇 번째 곡까지 분석됐나?

## 권장 상태 chip

```text
AutoMix  ● 분석 4 / 12
AutoMix  ◐ 4곡까지 적용
AutoMix  ✓ 최종 믹스
AutoMix  ! 일반 재생으로 대체
```

클릭하면 transition details/AutoMix details panel을 확장합니다.

색상만으로 상태를 전달하지 말고 icon + text + tooltip + accessible name을 함께 사용합니다.

---

# 5. fallback은 “오류”보다 “현재 적용 결과”를 설명

좋지 않은 메시지:

```text
AutoMix error.
```

권장:

```text
AutoMix 분석을 완료하지 못한 2곡은 일반 크로스페이드로 연결합니다.
미리보기는 계속 재생할 수 있습니다.

[세부 정보]
```

상태 chip:

```text
AutoMix · 10/12 적용 · 2곡 일반 전환
```

---

# 6. [P1] README / Help / UI 설명 동기화

README에는 아직 AutoMix가 Preview에 미적용이라고 적혀 있지만 실제 구현에는 progressive Preview가 존재합니다.

권장 capability table:

```markdown
| 기능 | Preview | Export |
|---|---|---|
| 일반 재생 | Yes | Yes |
| Crossfade | Yes | Yes |
| AutoMix geometry | Yes | Yes |
| AutoMix DSP | Yes | Yes |
| Progressive partial mix | Yes | N/A |
```

release checklist:

```text
[ ] README 주요 기능 설명이 현재 UI와 일치
[ ] Help의 기능 제한 설명이 현재 구현과 일치
[ ] Settings에 노출된 이름과 문서 용어가 일치
```

---

# 7. 디자인 문서 stale 값 정리

`docs/STUDIO_DESIGN.md`에는 기본 panel width가:

- source 272px
- inspector 316px

이라고 적혀 있습니다.

현재 `MainWindow`는:

```python
_DEFAULT_LEFT_PANEL_WIDTH = 300
_DEFAULT_RIGHT_PANEL_WIDTH = 380
_DEFAULT_BOTTOM_PANEL_HEIGHT = 240
```

입니다.

둘 중 하나를 authoritative하게 정해야 합니다.

권장:

```python
# app/ui/layout_metrics.py
LEFT_PANEL_DEFAULT = 300
RIGHT_PANEL_DEFAULT = 380
BOTTOM_PANEL_DEFAULT = 240
```

문서는 이 값을 설명만 하도록 합니다.

---

# 8. FFmpeg first-run 경험

FFmpeg 자동 설치 자체는 잘 구현되어 있지만 사용자는 README 기준으로 직접 `도구 → 설정 → FFmpeg`로 이동해야 합니다.

Export 클릭 시 FFmpeg 없음:

```text
영상 내보내기에 FFmpeg가 필요합니다.

Playlist Canvas가 검증된 FFmpeg를 자동으로 설치할 수 있습니다.

[지금 설치]  [직접 선택]  [취소]
```

처럼 context 안에서 해결하는 것이 좋습니다.

---

# 9. Preview 준비 dialog의 adaptive wait

현재 사용자가 “기다리지 않고 시작”할 수 있는 것은 좋은 설계입니다.

권장:

```text
0~300ms
  dialog 미표시

300ms 이후
  compact progress

2~3초 이후
  "기다리지 않고 미리보기 시작" CTA 강조
```

AutoMix progressive mode는 기술적으로 “Preview 먼저 열기 → 분석/partial/final hot-swap” 기반이 이미 있으므로 UX 정책을 더 비동기적으로 가져갈 여지가 있습니다.

---

# 10. 긴 작업 UI 통합

Project save/load, Preview mix, update, FFmpeg install, export에 공통 activity model을 권장합니다.

```python
@dataclass(frozen=True)
class ActivityState:
    id: str
    label: str
    detail: str
    fraction: float | None
    cancellable: bool
    severity: str = "normal"
```

표시 문법:

```text
작업 이름
현재 단계 · 상세
████████░░ 78%
[취소]
```

---

# 11. Export 진행 UX

사용자에게 내부 FFmpeg graph를 모두 보여줄 필요는 없지만 큰 단계는 보여주는 편이 좋습니다.

```text
1. 프로젝트 확인
2. 화면 준비
3. 오디오 믹스 준비
4. 영상 인코딩
5. 결과 검증
```

AutoMix일 때:

```text
오디오 믹스 준비
분석 캐시 10/12 · 2곡 분석 중
```

기술 로그는 “세부 정보”에 둡니다.

---

# 12. Error dialog에 recovery action 포함

## FFmpeg 누락

```text
[자동 설치] [설정 열기]
```

## 임시 디스크 부족

```text
임시 드라이브에 약 8.4 GB가 더 필요합니다.

[임시 폴더 열기] [확인]
```

## 누락 미디어

```text
[폴더에서 찾기] [이 항목 건너뛰기]
```

## AutoMix 분석 실패

```text
[일반 크로스페이드로 계속] [세부 정보]
```

---

# 13. Inspector 정보 밀도

추천 계층:

```text
기본
  위치 / 크기 / 회전 / 불투명도

스타일
  색상 / 테두리 / 그림자

콘텐츠별
  가사 / 트랙 목록 / 비주얼라이저 ...

애니메이션
  시작 / 종료 / 반복

고급
  드물게 사용하는 값
```

기존 사용자의 click path를 크게 바꾸는 재배치는 usability test 후 적용합니다.

---

# 14. 슬라이더 + 숫자 입력 패턴 유지

`STUDIO_DESIGN.md`의:

- 연속값 → slider + numeric input
- 좌표/레이어 순서 → numeric input

규칙은 좋습니다.

추가 후보:

- fine adjustment
- reset-to-default
- 변경된 값 indicator
- multi-selection mixed value의 `—` 표시

---

# 15. 접근성 자동 coverage 검사

현재 accessible name 기반은 이미 있습니다.

다음 단계는 자동 coverage입니다.

```python
def assert_interactive_widgets_have_accessible_names(root):
    for widget in root.findChildren(QWidget):
        if not is_interactive(widget):
            continue

        assert (
            widget.accessibleName().strip()
            or widget.toolTip().strip()
            or widget.text().strip()
        ), repr(widget)
```

대상:

- icon-only toolbar button
- color swatch
- slider
- playback control
- disclosure toggle
- custom canvas control

---

# 16. Focus 복원

권장:

- Preview 진입 → playback/preview focus
- Preview 종료 → 진입 전 focus 복원
- dialog 종료 → dialog를 연 버튼으로 복귀
- panel 접기 → 숨겨진 child에 focus를 남기지 않음

```python
@dataclass
class PreviewUiLockState:
    ...
    previous_focus: QWidget | None
```

---

# 17. AutoMix preset 설명

```text
Auto
곡에 맞게 자동 선택

Smooth
길고 부드러운 전환, 플레이리스트 감상용

Energetic
에너지 손실을 줄인 빠른 전환

DJ
박자 정렬과 적극적인 믹스를 우선
```

단, human listening tuning이 남아 있으므로 과도한 품질 보장 문구는 피합니다.

---

# 18. Human listening QA

평가 폼 예:

```text
Transition: A → B
Preset:
Style selected:

1. 타이밍 자연스러움       1 2 3 4 5
2. 보컬 충돌               1 2 3 4 5
3. 저역 충돌               1 2 3 4 5
4. 에너지 끊김             1 2 3 4 5
5. 전환 길이               짧음 / 적절 / 김
6. 전체 선호               A/B 중 선택
7. 코멘트
```

장르:

- K-pop
- EDM
- hip-hop
- rock
- ballad
- acoustic
- half/double tempo
- 긴 intro/outro
- vocal-heavy transition

preset/DSP parameter 변경의 근거로 사용합니다.

---

# 19. 기술 정보는 progressive disclosure

기본:

```text
AutoMix · 준비 중
```

세부:

```text
BPM 128 → 126
Effective BPM 128 → 126
Transition 8 bars
Style FILTER_BLEND
Fallback none
```

기술적 explainability는 유지하면서 초보자에게는 복잡도를 숨깁니다.

---

# 20. UX 적용 순서

## 즉시

1. README/Help AutoMix Preview 설명 수정
2. design doc panel width 수정
3. Preview action lock 범위 축소
4. fallback/status chip

## 다음

5. Project load background worker + progress/cancel
6. FFmpeg missing CTA
7. activity progress model 통합
8. focus restoration

## 이후

9. Inspector section hierarchy
10. AutoMix human listening 기반 preset tuning
11. keyboard/accessibility regression suite

---

# 21. 성공 지표

telemetry를 사용한다면 개인 데이터를 수집하지 않는 범위에서 다음을 볼 수 있습니다.

- project load duration bucket
- load cancel rate
- preview preparation skip-wait rate
- final AutoMix 준비 전 Preview 종료 비율
- AutoMix fallback count
- export failure category
- FFmpeg missing → install completion rate

telemetry를 사용하지 않는다면 QA에서 같은 지표를 수동 측정해도 됩니다.

---

# 22. 결론

현재 Playlist Canvas UI의 문제는 디자인 언어 부재가 아닙니다.

디자인 시스템과 접근성 기초는 이미 존재합니다.

다음 UX 개선의 핵심은:

1. 기다리는 동안 앱이 살아 있다는 확신
2. 현재 어떤 오디오/전환 모드인지 명확한 상태
3. Preview가 앱 전체를 봉쇄하지 않는 작업 모드
4. 오류 이후 바로 이어지는 recovery action
5. 실제 구현과 문서가 같은 말을 하는 것

입니다.
