# 다음 세션에 붙여 넣을 프롬프트

Playlist Canvas의 AutoMix를 전문 편집 프로그램 수준으로 확장해 주세요. 이번 세션은 사용자의 토큰 절약 요청에 따라 기본 분리만 했습니다. 아래 구현 상태를 먼저 확인하고 이어서 실제 구현과 검증까지 진행하세요.

## 최우선 추가 요청: 기존 편집 UI/UX 전면 재설계
기존 AutoMix 커스텀 편집기의 UI/UX를 갈아엎어 주세요. 기존 폼을 별도 창으로 옮기거나 색상·여백만 다듬는 것으로 완료하지 마세요. 현재 독립 창은 임시 구조일 뿐이며, 실제 전문 오디오 편집기/DAW처럼 정밀하게 보고, 선택하고, 조작하고, 들어볼 수 있는 작업 공간으로 새로 설계하고 구현해야 합니다. 기존 오디오 엔진과 저장 데이터는 보존·재사용하되 기존 화면 구성에는 얽매이지 마세요. 시각적 완성도와 인터랙션 완성도 모두 필수입니다.

- 상단에는 전환 선택, 단순/고급 모드, undo/redo 및 핵심 도구를 정돈하고, 중앙에는 충분히 넓은 두 곡 타임라인, 측면에는 선택 대상의 속성, 하단에는 부분 미리보기 transport를 배치하세요. 패널 크기 조절과 접기를 지원하고 타임라인 작업 면적을 우선하세요.
- 실제 오디오에서 얻은 두 곡 파형, 시간 눈금과 비트/마디 그리드, 곡/클립 이름, 재생 헤드, 큐 마커, 겹침 영역, 선택·루프 범위, 드래그 핸들, 페이드 곡선 및 대역별 자동화 레인을 시각적으로 표현하세요. 가짜 파형이나 렌더에 반영되지 않는 장식용 곡선을 사용하지 마세요.
- 나가는 곡과 들어오는 곡의 일관된 색 구분, 가독성 높은 타이포그래피, 정렬된 수치와 단위, 절제된 배경·구분선·강조색, 통일된 아이콘을 적용하세요. hover/focus/selected/disabled/dragging 상태가 명확해야 하며 색상만으로 의미를 전달하지 마세요.
- 드래그 시 시간/값 안내, 스냅 가이드, 정밀 수치 입력, zoom/pan 및 타임라인 탐색, 안정적인 선택 유지, 키보드 포커스와 넉넉한 핸들 hit area를 구현하세요. 값 변경이나 분석 완료 때문에 레이아웃이 튀거나 편집 대상이 바뀌지 않게 하세요.
- 단순 모드는 스타일·길이·핵심 큐·듣기 위주로 쉽게 구성하고, 고급 모드는 같은 작업 내용을 유지한 채 정밀 타이밍·대역별 편집·템포 등 필요한 레인을 확장하세요. 모드 전환이 기존 편집값을 초기화해서는 안 됩니다.
- 분석 중, 구간 렌더 중, 준비됨, 변경 후 미리보기 갱신 필요, 실패, 빈 상태를 구분해 표시하세요. 짧고 기능적인 시각 피드백을 사용하고 과한 애니메이션이나 불필요한 카드 중첩은 피하세요.
- 실제 실행 화면으로 작은 노트북 화면과 고해상도/DPI 환경을 확인하고, 단순/고급·선택/드래그·준비/실패 상태의 스크린샷으로 시각 QA를 수행하세요. 기능만 붙이고 시각 검증을 생략하지 마세요.

## 미리보기의 전환 상세 폼: AutoMix 믹싱 정보 전용
미리보기의 진입점은 “AutoMix 전환 자세히 보기”로 하고, 상세 폼에서는 현재 재생에 실제 적용된 AutoMix의 자세한 믹싱 정보만 볼 수 있게 하세요. 커스텀 편집은 독립 AutoMix 편집기에서만 수행합니다.

- 현재 전환의 두 곡, 적용 스타일/전략과 선택 근거, 시작/끝과 겹침 길이, 원본 큐 위치, BPM·템포 변화·램프, 대역별 교대/페이드, 보컬 보호 여부 등 실제 계획과 DSP에서 확인 가능한 정보를 읽기 전용으로 보여 주세요.
- 믹싱 과정을 이해할 수 있는 두 곡 겹침 도식, 페이드/EQ 타이밍, 현재 재생 위치 등의 시각적 설명을 포함하세요. 편집용 드래그 핸들, 수정 가능한 숫자 입력, 자동/수동 및 단순/고급 전환, 스타일 선택, 적용/초기화 버튼은 이 폼에서 제거하세요.
- 일반 곡 메타데이터나 무관한 프로젝트 설정으로 화면을 채우지 마세요. 핵심 믹싱 요약에서 정밀 수치와 선택 근거로 자연스럽게 내려가도록 정보 위계를 설계하세요.
- 재생 중인 실제 plan을 기준으로 표시하고 미적용 편집 초안을 실제 믹싱 결과처럼 보여 주지 마세요. 임시 계획·fallback·분석 미완료·정보 없음은 명확히 구분하며 수치를 추측하지 마세요.
- 상세 폼을 열거나 전환 항목을 선택하는 것만으로 프로젝트 변경, 재분석, 오디오 재렌더가 발생하지 않아야 합니다. 편집창과 상세창의 데이터 및 상태 흐름을 분리하고 회귀 테스트로 확인하세요.

## 사용자 목표
- 미리보기는 AutoMix 전환 자세히 보기(읽기 전용), 커스텀은 별도의 AutoMix 편집기.
- 수동 단순/고급 모드. 초보자는 스타일과 길이 중심, 고급은 정밀한 타임라인 편집.
- 풍부한 편집 선택지 및 단축키.
- 편집창에서 두 곡의 전환을 수정하면 전체 플레이리스트 오디오를 다시 불러오지 말고 수정한 구간만 준비해서 들을 것.

## 현재 구현
- app/controllers/preview_controller.py의 edit_transition은 이제 AutoMixEditorDialog를 열며 미리보기 탭으로 이동하지 않음.
- app/dialogs/automix_editor_dialog.py: 기존 TransitionInspectorWindow를 재사용한 독립 창. 프로젝트 변경과 충돌을 줄이기 위해 WindowModal. 기존 override 저장 경로를 연결했고 닫을 때 숫자 입력 디바운스를 flush함.
- 미리보기 ExportPreviewDialog의 상세창에는 enable_editing을 연결하지 않음.
- 단순/고급은 기존 advanced_box 재사용. Ctrl+1/2 및 Alt+Left/Right. 자동→수동 버튼을 누르면 기존 스타일, 큐, 템포, 대역 타이밍을 편집 가능.
- 오디오 재생 컨트롤은 숨겼으며 부분 미리보기가 미지원임을 명시. 부분 렌더링, 새로운 DSP, 전용 undo/redo는 아직 구현하지 않았음.
- tests/test_automix_editor_dialog.py는 미리보기 미진입, 모드, 닫기 시 저장을 검증함.

## 먼저 해결할 구조적 한계
1. 현재는 inspector 상속에 의존한 임시 틀이다. 위 전면 재설계 요구에 맞춰 편집기 전용 화면을 구현하고, 공유할 타임라인/컨트롤만 적절히 분리하라. 기존 inspector 레이아웃을 그대로 유지하는 것은 완료 조건이 아니다. 읽기 전용 상세에 편집 상태를 섞지 말 것.
2. 기존 _drafts는 선택 구간 표시용이며 전체 plan은 처음 열 때의 스냅샷이다. 길이/큐 변경이 다음 전환의 위치와 ramp_floor에 영향을 주는 문제를 해결하라. CPU 계획 갱신과 오디오 전체 렌더링은 구분할 것.
3. 디바운스 중 전환 선택 변경 시 입력 유실, 저장 실패, 창 종료 및 프로젝트 전환, 열려 있는 기존 미리보기 캐시 무효화도 검증할 것.
4. 독립 편집기에서 명확한 단순/고급 선택 UI 및 플레이리스트 전체용 진입 버튼을 추가하라. 현재 진입은 곡 사이 전환 칩이다.

## 부분 미리보기 우선 구현
- 추적 시작: app/widgets/transition_editor.py의 draft_junction, app/automix/planner.py의 plan_manual_junction/compile_automix, app/automix/renderer.py, app/renderer/ffmpeg_renderer.py, app/controllers/progressive_automix_controller.py, app/dialogs/export_preview_dialog.py의 remix_automix.
- 선택 전환의 앞뒤 여유 구간을 포함하는 렌더 요청을 만들고 두 소스에서 필요한 범위만 읽어 렌더. 템포 램프/인접 전환/소스 trim과 실제 export 결과의 일치를 보장할 것.
- 편집 UI 스레드에서 FFmpeg나 분석을 실행하지 말 것. generation ID로 오래된 완료 결과 무시, 디바운스/취소, 캐시 키(소스 identity/trim/override/분석/설정) 및 임시파일 수명 관리.
- 로컬 오디오 시간과 전체 타임라인 시간을 명시적으로 매핑. 수정 전 오디오 유지, 준비 후 안전한 교체, 선택 구간 루프, Space 재생, 탐색/볼륨. 실패 이유 표시 및 재시도.
- 프로젝트 저장을 매번 전체 preview remix 트리거와 연결하지 말 것. 최종 내보내기는 동일 override와 DSP를 사용해야 한다.

## 고급 편집 후속 범위
- 두 곡 파형, 비트/마디 그리드, zoom/pan, cue/overlap handles, 스냅 토글 및 Shift 임시 해제.
- 실제 renderer가 지원하는 것부터: 대역별 페이드 타이밍/곡선, gain, 필터, 템포 램프. 새 설정은 모델 validation/직렬화/호환성/렌더까지 연결하고 작동하지 않는 UI만 만들지 말 것.
- undo/redo(드래그 1회 = 1 command), 전환 설정 복사/붙여넣기, 초기화, A/B 비교, 사용자 프리셋.
- 키보드 도움말과 입력 포커스 보호. Ctrl+Z/Shift+Z, 이전/다음 전환, zoom, cue nudge, snapping, loop. 입력창에서 단축키가 숫자 입력을 가로채지 않게 할 것.
- 고정된 레이아웃, 스크롤, 작은 화면/DPI, 한국어/영어, 접근성 검증.

## 작업 보존 및 검증
시작 전 git diff를 읽을 것. 이번 작업 전부터 project_settings_dialog.py, automix_details_panel.py, transition_editor.py, transition_inspector.py 및 관련 테스트에 사용자 변경이 있었다. 되돌리지 말 것. docs/Playlist-Canvas-improvement-prompts.zip도 기존 파일이다.

Windows PowerShell 환경. 이번 세션에서는 .venv와 .build-venv312 모두 원래 Python 실행 파일 문제로 실행되지 않았다. 기본 python에는 PySide6가 없고 build site-packages를 연결해도 pytest가 없었다. GUI 테스트는 미실행이며 먼저 실행 환경을 복구해야 한다. 변경 파일의 py_compile 문법 검사는 통과했다.
검증 명령: `.build-venv312/Scripts/python.exe -m pytest tests/test_automix_editor_dialog.py tests/test_automix_manual_transitions.py tests/test_automix_details_panel.py tests/test_transition_inspector.py tests/test_main_window_preview.py -q`
부분 렌더는 전체 렌더 호출 횟수 0, stale 결과 무시, 취소/종료, 인접 전환, trim/tempo 매핑을 테스트하고 실제 오디오 및 작은 화면에서 수동 QA도 수행하라.
